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
        private readonly object _fileLock = new object();

        private AudioSocket? _audioSocket;
        private VideoSocket? _videoSocket;
        private FileStream? _recordingFileStream;
        private BinaryWriter? _recordingBinaryWriter;

        private string? _currentFilePath;
        private long _pcmDataBytesWritten;
        private bool _isRecording;
        private bool _disposed;

        // Diagnostic Tracking Fields
        private long _callbackCount;
        private long _totalPcmBytesReceived;
        private long _nonZeroSampleCount;
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
        }

        public bool IsRecording => _isRecording;
        public string? CurrentFilePath => _currentFilePath;
        public string? CurrentMp4FilePath => _currentMp4FilePath;
        public AudioSocket? AudioSocket => _audioSocket;
        public VideoSocket? VideoSocket => _videoSocket;

        public (AudioSocket audioSocket, VideoSocket videoSocket) CreateSockets()
        {
            if (_audioSocket != null && _videoSocket != null)
                return (_audioSocket, _videoSocket);

            var sharedCallId = Guid.NewGuid().ToString("N");

            if (_audioSocket == null)
            {
                var audioSettings = new AudioSocketSettings
                {
                    CallId = sharedCallId,
                    StreamDirections = StreamDirection.Recvonly,
                    SupportedAudioFormat = AudioFormat.Pcm44KStereo,
                };
                _audioSocket = new AudioSocket(audioSettings);
                _audioSocket.AudioMediaReceived += OnAudioMediaReceived;
                _logger.LogInformation("[Audio] AudioSocket created (CallId={CallId}, Recvonly, Pcm44KStereo Full HD Audio).", sharedCallId);
            }

            if (_videoSocket == null)
            {
                var videoSettings = new VideoSocketSettings
                {
                    CallId = sharedCallId,
                    StreamDirections = StreamDirection.Recvonly,
                    ReceiveColorFormat = VideoColorFormat.NV12,
                };
                _videoSocket = new VideoSocket(videoSettings);
                _videoSocket.VideoMediaReceived += OnVideoMediaReceived;
                _logger.LogInformation("[Video] VideoSocket created (CallId={CallId}, Recvonly, NV12).", sharedCallId);
            }

            return (_audioSocket, _videoSocket);
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
            lock (_fileLock)
            {
                if (_isRecording)
                {
                    _logger.LogInformation("[Recording] StartRecording called while already recording. Finalizing previous recording first...");
                    StopRecording();
                }

                var safeEventId = System.Text.RegularExpressions.Regex.Replace(eventId ?? "event", @"[^a-zA-Z0-9_-]", "_");
                if (safeEventId.Length > 20) safeEventId = safeEventId.Substring(0, 20);

                var fullDirectory = Path.IsPathRooted(recordingDirectory)
                    ? recordingDirectory
                    : Path.Combine(AppDomain.CurrentDomain.BaseDirectory, recordingDirectory);

                if (!Directory.Exists(fullDirectory))
                {
                    Directory.CreateDirectory(fullDirectory);
                    _logger.LogInformation("[Recording] Created recording directory: {Directory}", fullDirectory);
                }

                var timestamp = DateTime.UtcNow.ToString("yyyyMMdd_HHmmss");
                var wavFileName = $"meeting_{safeEventId}_{timestamp}.wav";
                var nv12FileName = $"meeting_{safeEventId}_{timestamp}.nv12";
                var mp4FileName = $"meeting_{safeEventId}_{timestamp}.mp4";

                _currentFilePath = Path.Combine(fullDirectory, wavFileName);
                _currentVideoFilePath = Path.Combine(fullDirectory, nv12FileName);
                _currentMp4FilePath = Path.Combine(fullDirectory, mp4FileName);

                _pcmDataBytesWritten = 0;
                _callbackCount = 0;
                _totalPcmBytesReceived = 0;
                _nonZeroSampleCount = 0;

                _videoCallbackCount = 0;
                _totalVideoBytesReceived = 0;
                _lastVideoWidth = 0;
                _lastVideoHeight = 0;
                _lastVideoFrameRate = 0;
                _lastVideoFormat = "NV12";

                _recordingStartTime = DateTime.UtcNow;

                _recordingFileStream = new FileStream(_currentFilePath, FileMode.Create, FileAccess.Write, FileShare.Read);
                _recordingBinaryWriter = new BinaryWriter(_recordingFileStream, Encoding.UTF8);

                _videoFileStream = new FileStream(_currentVideoFilePath, FileMode.Create, FileAccess.Write, FileShare.Read);
                _videoBinaryWriter = new BinaryWriter(_videoFileStream, Encoding.UTF8);

                WriteWavHeader(_recordingBinaryWriter, sampleRate: 44100, channels: 2, bitsPerSample: 16, pcmDataLength: 0);

                _isRecording = true;
                _logger.LogInformation("[Recording] Started Audio (WAV) + Video (NV12) stream session at {StartTime:u}", _recordingStartTime);
                _logger.LogInformation("[Recording] Audio Path : {AudioPath}", _currentFilePath);
                _logger.LogInformation("[Recording] Video Path : {VideoPath}", _currentVideoFilePath);
                _logger.LogInformation("[Recording] Final MP4   : {Mp4Path}", _currentMp4FilePath);
                return _currentFilePath;
            }
        }

        public void StopRecording()
        {
            lock (_fileLock)
            {
                if (!_isRecording)
                {
                    _logger.LogInformation("[Recording] StopRecording called, but no active recording session was running.");
                    return;
                }

                _recordingStopTime = DateTime.UtcNow;
                try
                {
                    _logger.LogInformation("[Recording] Finalizing media recording session. StartTime: {Start:u}, StopTime: {Stop:u}", _recordingStartTime, _recordingStopTime);
                    _logger.LogInformation("[Recording] Audio Diagnostics: Callbacks: {Count}, RecvBytes: {RecvBytes}, WritBytes: {WritBytes}, NonZero: {NonZero}",
                        _callbackCount, _totalPcmBytesReceived, _pcmDataBytesWritten, _nonZeroSampleCount);
                    _logger.LogInformation("[Recording] Video Diagnostics: Callbacks: {Count}, RecvBytes: {RecvBytes}, LastRes: {W}x{H}, Format: {Fmt}",
                        _videoCallbackCount, _totalVideoBytesReceived, _lastVideoWidth, _lastVideoHeight, _lastVideoFormat);

                    if (_recordingFileStream != null && _recordingBinaryWriter != null)
                    {
                        _recordingFileStream.Seek(0, SeekOrigin.Begin);
                        WriteWavHeader(_recordingBinaryWriter, sampleRate: 44100, channels: 2, bitsPerSample: 16, pcmDataLength: (uint)_pcmDataBytesWritten);
                        _recordingBinaryWriter.Flush();
                        _recordingFileStream.Flush();
                        _logger.LogInformation("[Recording] WAV file finalized: {FilePath}", _currentFilePath);
                    }

                    if (_videoFileStream != null && _videoBinaryWriter != null)
                    {
                        _videoBinaryWriter.Flush();
                        _videoFileStream.Flush();
                        _logger.LogInformation("[Recording] Raw Video (NV12) finalized: {FilePath}", _currentVideoFilePath);
                    }
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "[Recording] Error finalizing stream headers.");
                }
                finally
                {
                    _recordingBinaryWriter?.Close();
                    _recordingFileStream?.Close();
                    _recordingBinaryWriter?.Dispose();
                    _recordingFileStream?.Dispose();

                    _videoBinaryWriter?.Close();
                    _videoFileStream?.Close();
                    _videoBinaryWriter?.Dispose();
                    _videoFileStream?.Dispose();

                    _recordingBinaryWriter = null;
                    _recordingFileStream = null;
                    _videoBinaryWriter = null;
                    _videoFileStream = null;
                    _isRecording = false;

                    // Trigger FFmpeg MP4 Muxing
                    if (_videoCallbackCount > 0 && _lastVideoWidth > 0 && _lastVideoHeight > 0 &&
                        !string.IsNullOrEmpty(_currentFilePath) && !string.IsNullOrEmpty(_currentVideoFilePath) && !string.IsNullOrEmpty(_currentMp4FilePath))
                    {
                        double durationSec = (_recordingStopTime.Value - (_recordingStartTime ?? DateTime.UtcNow)).TotalSeconds;
                        double fps = durationSec > 0 ? (_videoCallbackCount / durationSec) : 15.0;
                        if (fps < 1.0) fps = 15.0;

                        _logger.LogInformation("[Recording] Initiating FFmpeg MP4 creation (Total Video Callbacks: {Count}, Duration: {Duration:F1}s, Calculated FPS: {Fps:F2})...",
                            _videoCallbackCount, durationSec, fps);

                        ConvertToMp4(_currentFilePath, _currentVideoFilePath, _currentMp4FilePath, _lastVideoWidth, _lastVideoHeight, fps);
                    }
                    else if (_videoCallbackCount == 0)
                    {
                        _logger.LogWarning("[Recording] No video frames were received during the call. MP4 creation skipped. Only WAV recording generated.");
                    }
                }
            }
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
                Interlocked.Increment(ref _callbackCount);

                using (var buffer = e.Buffer)
                {
                    if (buffer == null || buffer.Length == 0)
                    {
                        _logger.LogDebug("[Audio] AudioMediaReceived fired but buffer is null or empty. Callback #{Count}", _callbackCount);
                        return;
                    }

                    IntPtr dataPtr = buffer.Data;
                    int dataLength = (int)buffer.Length;

                    if (dataPtr == IntPtr.Zero || dataLength <= 0)
                    {
                        _logger.LogDebug("[Audio] AudioMediaReceived fired but DataPtr is Zero. Callback #{Count}", _callbackCount);
                        return;
                    }

                    byte[] pcmBytes = new byte[dataLength];
                    Marshal.Copy(dataPtr, pcmBytes, 0, dataLength);

                    Interlocked.Add(ref _totalPcmBytesReceived, dataLength);

                    // Inspect frame for non-zero PCM samples
                    long frameNonZeroCount = 0;
                    for (int i = 0; i < pcmBytes.Length; i++)
                    {
                        if (pcmBytes[i] != 0)
                            frameNonZeroCount++;
                    }
                    Interlocked.Add(ref _nonZeroSampleCount, frameNonZeroCount);

                    if (_callbackCount == 1 || _callbackCount % 50 == 0)
                    {
                        _logger.LogInformation("[Audio] AudioMediaReceived fired! Callback #{Count}, Frame Len: {Len} bytes, Frame NonZero Samples: {FrameNonZero}, Total Bytes Recv: {TotalBytes}",
                            _callbackCount, dataLength, frameNonZeroCount, _totalPcmBytesReceived);
                    }

                    WritePcmFrame(pcmBytes);
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "[Audio] Error inside AudioMediaReceived callback handling PCM frame.");
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

            lock (_fileLock)
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

            lock (_fileLock)
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

        private static void WriteWavHeader(BinaryWriter writer, int sampleRate, ushort channels, ushort bitsPerSample, uint pcmDataLength)
        {
            ushort blockAlign = (ushort)(channels * (bitsPerSample / 8));
            uint byteRate = (uint)(sampleRate * blockAlign);
            uint chunkSize = 36 + pcmDataLength;

            writer.Write(Encoding.ASCII.GetBytes("RIFF"));
            writer.Write(chunkSize);
            writer.Write(Encoding.ASCII.GetBytes("WAVE"));

            writer.Write(Encoding.ASCII.GetBytes("fmt "));
            writer.Write(16);
            writer.Write((ushort)1);
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
            if (!_disposed)
            {
                StopRecording();

                if (_audioSocket != null)
                {
                    _audioSocket.AudioMediaReceived -= OnAudioMediaReceived;
                    _audioSocket.Dispose();
                    _audioSocket = null;
                }

                _disposed = true;
            }
        }
    }
}
