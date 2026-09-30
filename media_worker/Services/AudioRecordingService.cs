using System;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using Microsoft.Skype.Bots.Media;

namespace MediaWorker.Services
{
    public class AudioRecordingService : IDisposable
    {
        private readonly ILogger<AudioRecordingService> _logger;
        private readonly object _syncLock = new object();

        private AudioSocket? _audioSocket;
        private VideoSocket? _videoSocket;
        private string? _currentCallId;
        private FileStream? _recordingFileStream;
        private BinaryWriter? _recordingBinaryWriter;

        private string? _currentFilePath;
        private long _pcmDataBytesWritten;
        private bool _isRecording;
        private bool _disposed;

        // Detected Audio Parameters
        private int _detectedSampleRate = 16000;
        private ushort _detectedChannels = 1;
        private ushort _detectedBitsPerSample = 16;

        // Diagnostic Tracking Fields
        private long _callbackCount;
        private long _totalPcmBytesReceived;
        private long _nonZeroSampleCount;
        private long _totalSampleCount;
        private int _peakAmplitude;
        private double _sumSquaredSamples;
        private long _silentFrameCount;
        private string? _firstFrameSamples;
        private string? _firstFrameBytesHex;
        private DateTime? _recordingStartTime;
        private DateTime? _recordingStopTime;

        // Video Recording Fields
        private FileStream? _videoFileStream;
        private BinaryWriter? _videoBinaryWriter;
        private string? _currentVideoFilePath;
        private string? _currentMp4FilePath;
        private long _videoCallbackCount;
        private long _totalVideoBytesReceived;
        private int _lastVideoWidth;
        private int _lastVideoHeight;
        private int _lastVideoFrameRate;
        private string _lastVideoFormat = "NV12";

        public AudioRecordingService(ILogger<AudioRecordingService> logger)
        {
            _logger = logger;
            InspectAudioSdkTypes();
        }

        private void InspectAudioSdkTypes()
        {
            try
            {
                _logger.LogInformation("=== [SDK Inspection] AudioSocketSettings & AudioFormat ===");
                var settingsType = typeof(AudioSocketSettings);
                foreach (var prop in settingsType.GetProperties(System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance))
                {
                    _logger.LogInformation("[SDK Property] AudioSocketSettings.{Name} ({Type})", prop.Name, prop.PropertyType.Name);
                }

                foreach (var name in Enum.GetNames(typeof(AudioFormat)))
                {
                    var val = Convert.ChangeType(Enum.Parse(typeof(AudioFormat), name), typeof(int));
                    _logger.LogInformation("[SDK Enum] AudioFormat.{Name} = {Value}", name, val);
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Error inspecting Audio SDK types");
            }
        }

        public bool IsRecording => _isRecording;
        public string? CurrentFilePath => _currentFilePath;
        public string? CurrentMp4FilePath => _currentMp4FilePath;
        public string? CurrentCallId => _currentCallId;
        public AudioSocket? AudioSocket => _audioSocket;
        public VideoSocket? VideoSocket => _videoSocket;
        public int DetectedSampleRate => _detectedSampleRate;
        public ushort DetectedChannels => _detectedChannels;

        private bool _hasNotifiedBackend;

        public (AudioSocket audioSocket, VideoSocket videoSocket) CreateSockets(bool forceNew = false)
        {
            return CreateSockets(callId: null, forceNew: forceNew);
        }

        public (AudioSocket audioSocket, VideoSocket videoSocket) CreateSockets(string? callId, bool forceNew = false)
        {
            lock (_syncLock)
            {
                if (_isRecording && _audioSocket != null && _videoSocket != null)
                {
                    _logger.LogInformation("[Sockets] Active recording session running. Returning existing sockets for Graph callId={CallId}.", _currentCallId);
                    return (_audioSocket, _videoSocket);
                }

                if (forceNew || _audioSocket == null || _videoSocket == null)
                {
                    if (_isRecording)
                    {
                        _logger.LogInformation("[Sockets] Active recording session running during CreateSockets(forceNew=true). Returning existing sockets for Graph callId={CallId}.", _currentCallId);
                        return (_audioSocket!, _videoSocket!);
                    }

                    DisposeSocketsInternal();

                    _currentCallId = !string.IsNullOrEmpty(callId) ? callId : Guid.NewGuid().ToString("N");

                    try
                    {
                        var audioSettings = new AudioSocketSettings
                        {
                            CallId = _currentCallId,
                            StreamDirections = StreamDirection.Recvonly,
                            SupportedAudioFormat = AudioFormat.Pcm44KStereo, // 44.1kHz Stereo PCM (Native Teams Meeting Audio)
                        };
                        _audioSocket = new AudioSocket(audioSettings);
                        _audioSocket.AudioMediaReceived += OnAudioMediaReceived;
                        _logger.LogInformation("[Sockets] AudioSocket created | HashCode={Hash} | Graph callId={CallId} | SupportedAudioFormat=Pcm44KStereo (44100Hz Stereo 16-bit PCM)", _audioSocket.GetHashCode(), _currentCallId);
                    }
                    catch (Exception ex)
                    {
                        _logger.LogWarning(ex, "[Sockets] AudioSocket initialization skipped or uninitialized in local environment without Skype MediaPlatform runtime (Graph callId={CallId}).", _currentCallId);
                    }

                    try
                    {
                        var videoSettings = new VideoSocketSettings
                        {
                            CallId = _currentCallId,
                            StreamDirections = StreamDirection.Recvonly,
                            ReceiveColorFormat = VideoColorFormat.NV12,
                        };
                        _videoSocket = new VideoSocket(videoSettings);
                        _videoSocket.VideoMediaReceived += OnVideoMediaReceived;
                        _logger.LogInformation("[Sockets] VideoSocket created | HashCode={Hash} | Graph callId={CallId} | Recvonly, NV12", _videoSocket.GetHashCode(), _currentCallId);
                    }
                    catch (Exception ex)
                    {
                        _logger.LogWarning(ex, "[Sockets] VideoSocket initialization skipped or uninitialized in local environment without Skype MediaPlatform runtime (Graph callId={CallId}).", _currentCallId);
                    }
                }

                return (_audioSocket!, _videoSocket!);
            }
        }

        public AudioSocket CreateAudioSocket()
        {
            return CreateSockets().audioSocket;
        }

        public VideoSocket CreateVideoSocket()
        {
            return CreateSockets().videoSocket;
        }

        public string StartRecording(string recordingDirectory, string eventId, string callId)
        {
            lock (_syncLock)
            {
                _logger.LogInformation("[Recording Request] StartRecording called | eventId={EventId} | Graph callId={CallId} | directory={Dir}", eventId, callId, recordingDirectory);

                if (_isRecording)
                {
                    bool isSameCall = !string.IsNullOrEmpty(callId) && string.Equals(_currentCallId, callId, StringComparison.OrdinalIgnoreCase);
                    bool isSameEvent = !string.IsNullOrEmpty(eventId) && string.Equals(_currentEventId, eventId, StringComparison.OrdinalIgnoreCase);

                    if (isSameCall || (string.IsNullOrEmpty(callId) && isSameEvent))
                    {
                        _logger.LogInformation("[Duplicate Start Ignored] eventId={EventId} | Graph callId={CallId}. Active recording session is already running for the SAME call at {Path}", eventId, callId, _currentFilePath);
                        return _currentFilePath ?? string.Empty;
                    }

                    _logger.LogWarning("[Recording Start] New call requested (Graph callId={NewCallId}, eventId={NewEventId}) while previous recording session (Graph callId={OldCallId}, eventId={OldEventId}) was still active. Finalizing previous recording now.",
                        callId, eventId, _currentCallId, _currentEventId);

                    StopRecordingInternal();
                }

                if (!string.IsNullOrEmpty(callId))
                {
                    _currentCallId = callId;
                }
                else if (string.IsNullOrEmpty(_currentCallId))
                {
                    _currentCallId = Guid.NewGuid().ToString("N");
                }

                if (_audioSocket == null || _videoSocket == null)
                {
                    CreateSockets(callId: _currentCallId, forceNew: false);
                }

                _currentEventId = eventId;
                _hasNotifiedBackend = false;
                var safeEventId = System.Text.RegularExpressions.Regex.Replace(eventId ?? "event", @"[^a-zA-Z0-9_-]", "_");
                if (safeEventId.Length > 20) safeEventId = safeEventId.Substring(0, 20);

                var safeCallId = System.Text.RegularExpressions.Regex.Replace(_currentCallId ?? "call", @"[^a-zA-Z0-9_-]", "_");
                if (safeCallId.Length > 12) safeCallId = safeCallId.Substring(0, 12);

                var fullDirectory = Path.IsPathRooted(recordingDirectory)
                    ? recordingDirectory
                    : Path.Combine(AppDomain.CurrentDomain.BaseDirectory, recordingDirectory);

                if (!Directory.Exists(fullDirectory))
                {
                    Directory.CreateDirectory(fullDirectory);
                    _logger.LogInformation("[Recording] Created recording directory: {Directory}", fullDirectory);
                }

                var timestamp = DateTime.UtcNow.ToString("yyyyMMdd_HHmmss");
                _logger.LogInformation("[MEDIA] Recording start requested\n[MEDIA] Event ID: {EventId}\n[MEDIA] Call ID: {CallId}", eventId, _currentCallId);

                var wavFileName = $"meeting_{safeEventId}_{safeCallId}_{timestamp}.wav";
                var nv12FileName = $"meeting_{safeEventId}_{safeCallId}_{timestamp}.nv12";
                var mp4FileName = $"meeting_{safeEventId}_{safeCallId}_{timestamp}.mp4";

                _currentFilePath = Path.Combine(fullDirectory, wavFileName);
                _currentVideoFilePath = Path.Combine(fullDirectory, nv12FileName);
                _currentMp4FilePath = Path.Combine(fullDirectory, mp4FileName);

                _pcmDataBytesWritten = 0;
                _callbackCount = 0;
                _totalPcmBytesReceived = 0;
                _nonZeroSampleCount = 0;
                _totalSampleCount = 0;
                _peakAmplitude = 0;
                _sumSquaredSamples = 0.0;
                _silentFrameCount = 0;
                _firstFrameSamples = null;
                _firstFrameBytesHex = null;

                _videoCallbackCount = 0;
                _totalVideoBytesReceived = 0;
                _lastVideoWidth = 0;
                _lastVideoHeight = 0;
                _lastVideoFrameRate = 0;
                _lastVideoFormat = "NV12";

                // Default initial settings until first frame arrives
                _detectedSampleRate = 16000;
                _detectedChannels = 1;
                _detectedBitsPerSample = 16;

                _recordingStartTime = DateTime.UtcNow;

                _recordingFileStream = new FileStream(_currentFilePath, FileMode.Create, FileAccess.Write, FileShare.Read);
                _recordingBinaryWriter = new BinaryWriter(_recordingFileStream, Encoding.UTF8);

                _videoFileStream = new FileStream(_currentVideoFilePath, FileMode.Create, FileAccess.Write, FileShare.Read);
                _videoBinaryWriter = new BinaryWriter(_videoFileStream, Encoding.UTF8);

                // Write initial placeholder WAV header
                WriteWavHeader(_recordingBinaryWriter, sampleRate: _detectedSampleRate, channels: _detectedChannels, bitsPerSample: _detectedBitsPerSample, pcmDataLength: 0);

                _isRecording = true;
                _logger.LogInformation(
                    "[MEDIA] Recording started\n" +
                    "[MEDIA] Recording file: {File}\n" +
                    "[MEDIA] Recording start time: {StartTime:u}",
                    _currentFilePath,
                    _recordingStartTime);
                return _currentFilePath;
            }
        }

        public void StopRecording()
        {
            lock (_syncLock)
            {
                if (!_isRecording)
                {
                    _logger.LogInformation("[Recording Stop] StopRecording called, but no active recording session was running.");
                    return;
                }

                StopRecordingInternal();
            }
        }

        private void StopRecordingInternal()
        {
            if (!_isRecording) return;

            // Stop accepting new audio/video frames immediately
            _isRecording = false;

            _recordingStopTime = DateTime.UtcNow;
            string? finalizedCallId = _currentCallId;
            string? finalizedEventId = _currentEventId;
            string? finalizedFilePath = _currentFilePath;
            string? finalizedVideoFilePath = _currentVideoFilePath;
            string? finalizedMp4FilePath = _currentMp4FilePath;
            long bytesWritten = _pcmDataBytesWritten;

            _logger.LogInformation("[MEDIA] Recording stop requested\n[MEDIA] Finalizing WAV");
            try
            {
                // Finalize WAV Header with exact detected sample rate, channel count, and bytes written
                if (_recordingFileStream != null && _recordingBinaryWriter != null)
                {
                    _recordingFileStream.Seek(0, SeekOrigin.Begin);
                    WriteWavHeader(_recordingBinaryWriter, sampleRate: _detectedSampleRate, channels: _detectedChannels, bitsPerSample: _detectedBitsPerSample, pcmDataLength: (uint)bytesWritten);
                    _recordingBinaryWriter.Flush();
                    _recordingFileStream.Flush();
                }

                if (_videoFileStream != null && _videoBinaryWriter != null)
                {
                    _videoBinaryWriter.Flush();
                    _videoFileStream.Flush();
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "[MEDIA] ERROR: Error finalizing stream headers for call {CallId}", finalizedCallId);
            }
            finally
            {
                try { _recordingBinaryWriter?.Close(); } catch { }
                try { _recordingFileStream?.Close(); } catch { }
                try { _recordingBinaryWriter?.Dispose(); } catch { }
                try { _recordingFileStream?.Dispose(); } catch { }

                try { _videoBinaryWriter?.Close(); } catch { }
                try { _videoFileStream?.Close(); } catch { }
                try { _videoBinaryWriter?.Dispose(); } catch { }
                try { _videoFileStream?.Dispose(); } catch { }

                _recordingBinaryWriter = null;
                _recordingFileStream = null;
                _videoBinaryWriter = null;
                _videoFileStream = null;

                // Run Audio Validation & Diagnostic Summary
                PerformAudioValidationReport();

                long finalWavSize = 0;
                if (!string.IsNullOrEmpty(finalizedFilePath) && File.Exists(finalizedFilePath))
                {
                    finalWavSize = new FileInfo(finalizedFilePath).Length;
                }
                double finalDurationSec = (_recordingStopTime.Value - (_recordingStartTime ?? DateTime.UtcNow)).TotalSeconds;
                _logger.LogInformation(
                    "[MEDIA] PCM bytes written: {Bytes}\n" +
                    "[MEDIA] Final file size: {Size}\n" +
                    "[MEDIA] Duration: {Duration:F2}s\n" +
                    "[MEDIA] WAV finalized successfully\n" +
                    "[MEDIA] Recording file: {File}",
                    bytesWritten, finalWavSize, finalDurationSec, finalizedFilePath);

                if (bytesWritten == 0 || (_nonZeroSampleCount == 0 && _callbackCount > 0))
                {
                    _logger.LogWarning("[MEDIA] WARNING: Recording contains no meaningful audio data");
                }

                // Trigger FFmpeg MP4 Muxing
                if (_videoCallbackCount > 0 && _lastVideoWidth > 0 && _lastVideoHeight > 0 &&
                    !string.IsNullOrEmpty(finalizedFilePath) && !string.IsNullOrEmpty(finalizedVideoFilePath) && !string.IsNullOrEmpty(finalizedMp4FilePath))
                {
                    double fps = finalDurationSec > 0 ? (_videoCallbackCount / finalDurationSec) : 15.0;
                    if (fps < 1.0) fps = 15.0;

                    ConvertToMp4(finalizedFilePath, finalizedVideoFilePath, finalizedMp4FilePath, _lastVideoWidth, _lastVideoHeight, fps);
                }

                DisposeSocketsInternal();

                if (!string.IsNullOrEmpty(finalizedFilePath))
                {
                    NotifyBackendRecordingStopped(finalizedCallId, finalizedEventId, Path.GetFileName(finalizedFilePath));
                }
            }
        }

        private string? _currentEventId;

        private void NotifyBackendRecordingStopped(string? callId, string? eventId, string? fileName)
        {
            if (string.IsNullOrEmpty(fileName)) return;
            lock (_syncLock)
            {
                if (_hasNotifiedBackend) return;
                _hasNotifiedBackend = true;
            }
            Task.Run(async () =>
            {
                try
                {
                    string backendUrl = Environment.GetEnvironmentVariable("PUBLIC_BACKEND_URL")
                        ?? Environment.GetEnvironmentVariable("BACKEND_URL")
                        ?? "http://localhost:8000";
                    string endpoint = $"{backendUrl.TrimEnd('/')}/api/v1/calls/recording-stopped";
                    _logger.LogInformation("[Webhook] Sending recording-stopped callback for Graph callId={CallId} | eventId={EventId} | file={FileName} to endpoint: {Endpoint}", callId, eventId, fileName, endpoint);
                    using (var client = new System.Net.Http.HttpClient { Timeout = TimeSpan.FromSeconds(10) })
                    {
                        var payload = new { callId = callId, eventId = eventId, fileName = fileName, filePath = fileName };
                        var json = Newtonsoft.Json.JsonConvert.SerializeObject(payload);
                        var content = new System.Net.Http.StringContent(json, Encoding.UTF8, "application/json");
                        var resp = await client.PostAsync(endpoint, content);
                        _logger.LogInformation("[Webhook] Successfully sent recording-stopped callback for Graph callId={CallId} | file={FileName}: Status={Status}", callId, fileName, resp.StatusCode);
                    }
                }
                catch (Exception ex)
                {
                    _logger.LogWarning(ex, "[Webhook] Could not notify backend of recording stopped for Graph callId={CallId} | file={FileName}.", callId, fileName);
                }
            });
        }

        private void DisposeSocketsInternal()
        {
            if (_audioSocket != null)
            {
                try
                {
                    _audioSocket.AudioMediaReceived -= OnAudioMediaReceived;
                    _audioSocket.Dispose();
                    _logger.LogInformation("[Sockets] Disposed AudioSocket for Graph callId={CallId}.", _currentCallId);
                }
                catch (Exception ex)
                {
                    _logger.LogWarning(ex, "[Sockets] Exception while disposing AudioSocket for Graph callId={CallId}.", _currentCallId);
                }
                finally
                {
                    _audioSocket = null;
                }
            }

            if (_videoSocket != null)
            {
                try
                {
                    _videoSocket.VideoMediaReceived -= OnVideoMediaReceived;
                    _videoSocket.Dispose();
                    _logger.LogInformation("[Sockets] Disposed VideoSocket for Graph callId={CallId}.", _currentCallId);
                }
                catch (Exception ex)
                {
                    _logger.LogWarning(ex, "[Sockets] Exception while disposing VideoSocket for Graph callId={CallId}.", _currentCallId);
                }
                finally
                {
                    _videoSocket = null;
                }
            }

            _currentCallId = null;
        }

        private void PerformAudioValidationReport()
        {
            long wavSize = 0;
            if (!string.IsNullOrEmpty(_currentFilePath) && File.Exists(_currentFilePath))
            {
                wavSize = new FileInfo(_currentFilePath).Length;
            }

            int bytesPerSample = _detectedBitsPerSample / 8;
            int bytesPerFrame = _detectedChannels * bytesPerSample;
            double durationSec = (bytesPerFrame > 0 && _detectedSampleRate > 0)
                ? (double)_pcmDataBytesWritten / (_detectedSampleRate * bytesPerFrame)
                : 0.0;

            double rmsAmplitude = _totalSampleCount > 0 ? Math.Sqrt(_sumSquaredSamples / _totalSampleCount) : 0.0;
            bool isSilent = (_pcmDataBytesWritten == 0) || (_nonZeroSampleCount == 0) || (_peakAmplitude < 50) || (rmsAmplitude < 10.0);

            var sb = new StringBuilder();
            sb.AppendLine();
            sb.AppendLine("==================================================");
            sb.AppendLine("[Audio Recording Summary & Validation]");
            sb.AppendLine($"  - Audio Socket Initialized : {(_audioSocket != null)}");
            sb.AppendLine($"  - Audio Frames Received    : {_callbackCount}");
            sb.AppendLine($"  - Audio Bytes Received     : {_totalPcmBytesReceived}");
            sb.AppendLine($"  - WAV File Path            : {_currentFilePath}");
            sb.AppendLine($"  - Sample Rate              : {_detectedSampleRate} Hz");
            sb.AppendLine($"  - Channel Count            : {_detectedChannels} ({(_detectedChannels == 1 ? "Mono" : "Stereo")})");
            sb.AppendLine($"  - Bits Per Sample          : {_detectedBitsPerSample}");
            sb.AppendLine($"  - Final WAV Size           : {wavSize} bytes");
            sb.AppendLine($"  - Final WAV Duration       : {durationSec:F2} sec");
            sb.AppendLine($"  - Peak Amplitude           : {_peakAmplitude} / 32767");
            sb.AppendLine($"  - RMS Amplitude            : {rmsAmplitude:F2}");
            sb.AppendLine($"  - Non-Zero Samples Count   : {_nonZeroSampleCount} / {_totalSampleCount}");
            sb.AppendLine($"  - Silent Frames Count       : {_silentFrameCount} / {_callbackCount}");
            sb.AppendLine($"  - Silent (Is Silent?)       : {isSilent}");
            sb.AppendLine("==================================================");

            _logger.LogInformation(sb.ToString());
        }

        private void ConvertToMp4(string wavPath, string nv12Path, string mp4Path, int width, int height, double fps)
        {
            try
            {
                string ffmpegExe = FindFFmpegExecutable();
                if (string.IsNullOrEmpty(ffmpegExe) || !File.Exists(ffmpegExe))
                {
                    _logger.LogWarning("[FFmpeg] ffmpeg.exe not found at '{Path}'. Skipping MP4 encoding. Raw WAV preserved at: {Wav}", ffmpegExe, wavPath);
                    return;
                }

                _logger.LogInformation("[FFmpeg] Starting MP4 encoding: {W}x{H} @ {Fps:F2} FPS using FFmpeg binary at: {FFmpegPath}", width, height, fps, ffmpegExe);

                string args = $"-y -f rawvideo -pix_fmt nv12 -s {width}x{height} -r {fps:F2} -i \"{nv12Path}\" -i \"{wavPath}\" -c:v libx264 -preset ultrafast -crf 23 -c:a aac -b:a 128k -movflags +faststart \"{mp4Path}\"";

                var psi = new System.Diagnostics.ProcessStartInfo
                {
                    FileName = ffmpegExe,
                    Arguments = args,
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    CreateNoWindow = true
                };

                using (var proc = System.Diagnostics.Process.Start(psi))
                {
                    if (proc == null)
                    {
                        _logger.LogError("[FFmpeg] Failed to launch FFmpeg process.");
                        return;
                    }

                    string stderr = proc.StandardError.ReadToEnd();
                    proc.WaitForExit(60000); // 60 second timeout

                    if (proc.ExitCode == 0 && File.Exists(mp4Path) && new FileInfo(mp4Path).Length > 0)
                    {
                        _logger.LogInformation("[FFmpeg] SUCCESS: Synchronized MP4 generated cleanly at: {Mp4Path} (Size: {Size} bytes)", mp4Path, new FileInfo(mp4Path).Length);
                        _currentMp4FilePath = mp4Path;

                        try
                        {
                            if (File.Exists(nv12Path))
                            {
                                File.Delete(nv12Path);
                                _logger.LogInformation("[Recording] Temporary raw video file cleaned up: {Nv12Path}", nv12Path);
                            }
                        }
                        catch { }
                    }
                    else
                    {
                        _logger.LogError("[FFmpeg] FFmpeg exited with code {Code}. Stderr output: {Stderr}", proc.ExitCode, stderr.Length > 500 ? stderr.Substring(stderr.Length - 500) : stderr);
                    }
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "[FFmpeg] Exception during MP4 encoding.");
            }
        }

        private string FindFFmpegExecutable()
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            string path1 = Path.Combine(baseDir, "ffmpeg.exe");
            if (File.Exists(path1)) return path1;

            string path2 = Path.Combine(baseDir, "bin", "ffmpeg.exe");
            if (File.Exists(path2)) return path2;

            return "ffmpeg.exe";
        }

        private void OnAudioMediaReceived(object sender, AudioMediaReceivedEventArgs e)
        {
            try
            {
                long currentCallback = Interlocked.Increment(ref _callbackCount);

                using (var buffer = e.Buffer)
                {
                    if (buffer == null || buffer.Length == 0)
                    {
                        _logger.LogDebug("[Audio] AudioMediaReceived fired but buffer is null or empty. Callback #{Count}", currentCallback);
                        return;
                    }

                    IntPtr dataPtr = buffer.Data;
                    int dataLength = (int)buffer.Length;

                    if (dataPtr == IntPtr.Zero || dataLength <= 0)
                    {
                        _logger.LogDebug("[Audio] AudioMediaReceived fired but DataPtr is Zero. Callback #{Count}", currentCallback);
                        return;
                    }

                    // Normalize and resample incoming Skype AudioFormat to 16,000 Hz Mono 16-bit PCM
                    var incomingFormat = buffer.AudioFormat;
                    byte[] pcmBytes = ProcessAndResampleAudioFrame(dataPtr, dataLength, incomingFormat);

                    _detectedSampleRate = 16000;
                    _detectedChannels = 1;
                    _detectedBitsPerSample = 16;

                    if (buffer.IsSilence)
                    {
                        Interlocked.Increment(ref _silentFrameCount);
                    }

                    Interlocked.Add(ref _totalPcmBytesReceived, dataLength);

                    // Analyze 16-bit signed PCM samples on normalized 16kHz Mono output
                    int sampleCountInFrame = pcmBytes.Length / 2;
                    long frameNonZeroCount = 0;
                    int framePeak = 0;
                    double frameSumSquare = 0.0;

                    short[] samplePreview = new short[Math.Min(8, sampleCountInFrame)];

                    lock (_syncLock)
                    {
                        for (int i = 0; i < sampleCountInFrame; i++)
                        {
                            short sample = BitConverter.ToInt16(pcmBytes, i * 2);
                            if (i < samplePreview.Length)
                            {
                                samplePreview[i] = sample;
                            }

                            if (sample != 0)
                            {
                                frameNonZeroCount++;
                            }

                            int absVal = Math.Abs((int)sample);
                            if (absVal > framePeak) framePeak = absVal;
                            if (absVal > _peakAmplitude) _peakAmplitude = absVal;

                            frameSumSquare += (double)sample * sample;
                        }

                        _nonZeroSampleCount += frameNonZeroCount;
                        _totalSampleCount += sampleCountInFrame;
                        _sumSquaredSamples += frameSumSquare;
                    }

                    if (currentCallback == 1 || currentCallback % 50 == 0)
                    {
                        double durationSec = (16000 * 2 > 0) ? (double)_pcmDataBytesWritten / (16000 * 2) : 0.0;
                        string nowIso = DateTime.UtcNow.ToString("o");
                        _logger.LogInformation(
                            "[MEDIA] Recording active\n" +
                            "[MEDIA] Call ID: {CallId}\n" +
                            "[MEDIA] Audio received: {Bytes} bytes ({Frames} frames)\n" +
                            "[MEDIA] Recording duration: {Duration:F1}s\n" +
                            "[MEDIA] Last audio received: {LastAudio}",
                            _currentCallId ?? "N/A",
                            _pcmDataBytesWritten,
                            currentCallback,
                            durationSec,
                            nowIso);
                    }

                    WritePcmFrame(pcmBytes);
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "[Audio] Error inside AudioMediaReceived callback handling PCM frame.");
            }
        }

        private byte[] ProcessAndResampleAudioFrame(IntPtr dataPtr, int dataLength, AudioFormat format)
        {
            if (format == AudioFormat.Pcm16K)
            {
                byte[] pcmBytes = new byte[dataLength];
                Marshal.Copy(dataPtr, pcmBytes, 0, dataLength);
                return pcmBytes;
            }
            else if (format == AudioFormat.Pcm44KStereo)
            {
                // 44.1kHz Stereo (2 ch * 2 bytes = 4 bytes per sample frame) -> 16kHz Mono (1 ch * 2 bytes = 2 bytes per sample)
                int numStereoSamples = dataLength / 4;
                if (numStereoSamples == 0) return Array.Empty<byte>();

                short[] mono44k = new short[numStereoSamples];
                for (int i = 0; i < numStereoSamples; i++)
                {
                    short left = Marshal.ReadInt16(dataPtr, i * 4);
                    short right = Marshal.ReadInt16(dataPtr, i * 4 + 2);
                    mono44k[i] = (short)((left + right) / 2);
                }

                double ratio = 44100.0 / 16000.0;
                int targetSampleCount = (int)(numStereoSamples / ratio);
                byte[] outputBytes = new byte[targetSampleCount * 2];

                for (int i = 0; i < targetSampleCount; i++)
                {
                    double inPos = i * ratio;
                    int idx = (int)inPos;
                    double frac = inPos - idx;

                    short s;
                    if (idx + 1 < numStereoSamples)
                    {
                        s = (short)(mono44k[idx] * (1.0 - frac) + mono44k[idx + 1] * frac);
                    }
                    else if (idx < numStereoSamples)
                    {
                        s = mono44k[idx];
                    }
                    else
                    {
                        s = 0;
                    }

                    outputBytes[i * 2] = (byte)(s & 0xFF);
                    outputBytes[i * 2 + 1] = (byte)((s >> 8) & 0xFF);
                }

                return outputBytes;
            }
            else
            {
                byte[] pcmBytes = new byte[dataLength];
                Marshal.Copy(dataPtr, pcmBytes, 0, dataLength);
                return pcmBytes;
            }
        }

        private void OnVideoMediaReceived(object sender, VideoMediaReceivedEventArgs e)
        {
            try
            {
                Interlocked.Increment(ref _videoCallbackCount);

                using (var buffer = e.Buffer)
                {
                    if (buffer == null || buffer.Length == 0)
                    {
                        _logger.LogDebug("[Video] VideoMediaReceived fired but buffer is null or empty. Callback #{Count}", _videoCallbackCount);
                        return;
                    }

                    IntPtr dataPtr = buffer.Data;
                    int dataLength = (int)buffer.Length;

                    if (dataPtr == IntPtr.Zero || dataLength <= 0)
                    {
                        _logger.LogDebug("[Video] VideoMediaReceived fired but DataPtr is Zero. Callback #{Count}", _videoCallbackCount);
                        return;
                    }

                    if (buffer.VideoFormat != null)
                    {
                        _lastVideoWidth = buffer.VideoFormat.Width;
                        _lastVideoHeight = buffer.VideoFormat.Height;
                        _lastVideoFormat = buffer.VideoFormat.VideoColorFormat.ToString();
                    }

                    byte[] videoBytes = new byte[dataLength];
                    Marshal.Copy(dataPtr, videoBytes, 0, dataLength);

                    Interlocked.Add(ref _totalVideoBytesReceived, dataLength);

                    if (_videoCallbackCount == 1 || _videoCallbackCount % 30 == 0)
                    {
                        _logger.LogInformation("[Video] VideoMediaReceived fired! Callback #{Count}, Res: {W}x{H}, Format: {Fmt}, Frame Len: {Len} bytes, Timestamp: {Timestamp}, Total Bytes: {Total}",
                            _videoCallbackCount, _lastVideoWidth, _lastVideoHeight, _lastVideoFormat, dataLength, buffer.Timestamp, _totalVideoBytesReceived);
                    }

                    WriteVideoFrame(videoBytes);
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "[Video] Error inside VideoMediaReceived callback.");
            }
        }

        public void WriteVideoFrame(byte[] videoBytes)
        {
            if (!_isRecording || videoBytes == null || videoBytes.Length == 0)
                return;

            lock (_syncLock)
            {
                if (!_isRecording || _videoBinaryWriter == null)
                    return;

                try
                {
                    _videoBinaryWriter.Write(videoBytes);
                    _videoBinaryWriter.Flush();
                    _videoFileStream?.Flush();
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "Error writing Video frame to recording stream.");
                }
            }
        }

        public void WritePcmFrame(byte[] pcmBytes)
        {
            if (!_isRecording || pcmBytes == null || pcmBytes.Length == 0)
                return;

            lock (_syncLock)
            {
                if (!_isRecording || _recordingBinaryWriter == null)
                    return;

                try
                {
                    _recordingBinaryWriter.Write(pcmBytes);
                    _pcmDataBytesWritten += pcmBytes.Length;
                    _recordingBinaryWriter.Flush();
                    _recordingFileStream?.Flush();
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "Error writing PCM frame to recording stream.");
                }
            }
        }

        public static void WriteWavHeader(BinaryWriter writer, int sampleRate, ushort channels, ushort bitsPerSample, uint pcmDataLength)
        {
            ushort blockAlign = (ushort)(channels * (bitsPerSample / 8));
            uint byteRate = (uint)(sampleRate * blockAlign);
            uint chunkSize = 36 + pcmDataLength;

            writer.Write(Encoding.ASCII.GetBytes("RIFF"));
            writer.Write(chunkSize);
            writer.Write(Encoding.ASCII.GetBytes("WAVE"));

            writer.Write(Encoding.ASCII.GetBytes("fmt "));
            writer.Write(16); // subchunk1 size (16 for PCM)
            writer.Write((ushort)1); // audio format (1 = PCM)
            writer.Write(channels);
            writer.Write(sampleRate);
            writer.Write(byteRate);
            writer.Write(blockAlign);
            writer.Write(bitsPerSample);

            writer.Write(Encoding.ASCII.GetBytes("data"));
            writer.Write(pcmDataLength);
        }

        public void Dispose()
        {
            lock (_syncLock)
            {
                if (!_disposed)
                {
                    StopRecordingInternal();
                    DisposeSocketsInternal();
                    _disposed = true;
                }
            }
        }
    }
}
