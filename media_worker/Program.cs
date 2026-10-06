using System;
using System.IO;
using System.Net;
using System.ServiceProcess;
using System.Text;
using System.Threading.Tasks;
using MediaWorker.Services;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace MediaWorker
{
    internal class Program
    {
        private static async Task Main(string[] args)
        {
            Directory.SetCurrentDirectory(AppDomain.CurrentDomain.BaseDirectory);
            LoadDotEnv();

            if (Array.Exists(args, a => a.Equals("--test", StringComparison.OrdinalIgnoreCase)))
            {
                AudioRecordingServiceTests.RunAllTests();
                return;
            }

            var host = Host.CreateDefaultBuilder(args)
                .UseWindowsService()
                .ConfigureServices((context, services) =>
                {
                    services.AddSingleton<AudioRecordingService>();
                    services.AddSingleton<MediaPlatformHost>();
                    services.AddHostedService(sp => sp.GetRequiredService<MediaPlatformHost>());
                })
                .Build();

            var logger = host.Services.GetRequiredService<ILogger<Program>>();
            var mediaHost = host.Services.GetRequiredService<MediaPlatformHost>();
            var recordingService = host.Services.GetRequiredService<AudioRecordingService>();

            int port = 5050;
            StartHttpBridge(port, mediaHost, recordingService, logger);

            logger.LogInformation("Starting TekMeet C# Media Worker Service (.NET Framework 4.8 win-x64)...");
            logger.LogInformation("HTTP Bridge Listener running on http://localhost:{Port}/", port);
            logger.LogInformation("[MEDIA] Runtime executable/base directory: {BaseDir}", AppDomain.CurrentDomain.BaseDirectory);
            logger.LogInformation("[MEDIA] Runtime current directory: {CurrentDir}", Directory.GetCurrentDirectory());
            logger.LogInformation("[MEDIA] Canonical recordings directory: {RecDir}", AudioRecordingService.GetCanonicalRecordingsDirectory());

            bool isWindowsService = !Environment.UserInteractive && Array.Exists(args, a => a.Equals("--service", StringComparison.OrdinalIgnoreCase));
            if (isWindowsService)
            {
                using (var service = new TekMeetServiceBridge(host))
                {
                    ServiceBase.Run(service);
                }
            }
            else
            {
                await host.RunAsync();
            }
        }

        private static void StartHttpBridge(int port, MediaPlatformHost mediaHost, AudioRecordingService recordingService, ILogger logger)
        {
            Task.Run(async () =>
            {
                var listener = new HttpListener();
                bool started = false;

                // Attempt 1: Wildcard listener for all network interfaces (requires Admin or URL ACL)
                try
                {
                    listener.Prefixes.Add($"http://+:{port}/");
                    listener.Start();
                    started = true;
                    logger.LogInformation("HTTP Bridge Listener started on http://+:{Port}/", port);
                }
                catch
                {
                    try { listener.Close(); } catch { }
                    listener = new HttpListener();
                }

                // Attempt 2: Localhost & 127.0.0.1 listener (works without Admin privileges)
                if (!started)
                {
                    try
                    {
                        listener.Prefixes.Add($"http://localhost:{port}/");
                        listener.Prefixes.Add($"http://127.0.0.1:{port}/");
                        listener.Start();
                        started = true;
                        logger.LogInformation("HTTP Bridge Listener started on http://localhost:{Port}/", port);
                    }
                    catch (Exception ex)
                    {
                        logger.LogWarning(ex, "Could not start HttpListener on port {Port}.", port);
                        return;
                    }
                }

                using (listener)
                {
                    while (listener.IsListening)
                    {
                        try
                        {
                            var ctx = await listener.GetContextAsync();
                            ProcessRequest(ctx, mediaHost, recordingService, logger);
                        }
                        catch (Exception ex)
                        {
                            logger.LogDebug("HttpListener exception: {Message}", ex.Message);
                        }
                    }
                }
            });
        }

        private static void ProcessRequest(HttpListenerContext ctx, MediaPlatformHost mediaHost, AudioRecordingService recordingService, ILogger logger)
        {
            try
            {
                var req = ctx.Request;
                var resp = ctx.Response;
                resp.ContentType = "application/json";

                var path = req.Url.AbsolutePath.ToLowerInvariant();

                if (path == "/health" || path == "/" || path == "/api/media/status")
                {
                    var payload = new {
                        status = "healthy",
                        isRecording = recordingService.IsRecording,
                        currentCallId = recordingService.CurrentCallId,
                        currentFilePath = recordingService.CurrentFilePath,
                        detectedSampleRate = recordingService.DetectedSampleRate,
                        detectedChannels = recordingService.DetectedChannels,
                        mediaPlatformInitialized = mediaHost.IsInitialized
                    };
                    string json = Newtonsoft.Json.JsonConvert.SerializeObject(payload);
                    byte[] buf = Encoding.UTF8.GetBytes(json);
                    resp.OutputStream.Write(buf, 0, buf.Length);
                    resp.Close();
                    return;
                }

                if (path == "/api/media/config")
                {
                    string blob = mediaHost.GetMediaConfigurationBlob();
                    var payload = new { blob = blob, initialized = mediaHost.IsInitialized };
                    string json = Newtonsoft.Json.JsonConvert.SerializeObject(payload);
                    byte[] buf = Encoding.UTF8.GetBytes(json);
                    resp.OutputStream.Write(buf, 0, buf.Length);
                    resp.Close();
                    return;
                }

                if (path == "/api/media/recording/start")
                {
                    string directory = req.QueryString["directory"] ?? "recordings";
                    string eventId = req.QueryString["eventId"] ?? "event_" + Guid.NewGuid().ToString("N").Substring(0, 8);
                    string callId = req.QueryString["callId"] ?? "call_" + Guid.NewGuid().ToString("N").Substring(0, 8);

                    string filePath = recordingService.StartRecording(directory, eventId, callId);
                    string json = $"{{\"status\":\"started\",\"filePath\":\"{filePath.Replace("\\", "\\\\")}\"}}";
                    byte[] buf = Encoding.UTF8.GetBytes(json);
                    resp.OutputStream.Write(buf, 0, buf.Length);
                    resp.Close();
                    return;
                }

                if (path == "/api/media/recording/stop")
                {
                    logger.LogInformation("[MEDIA] STOP callId={CallId}", recordingService.CurrentCallId ?? "N/A");
                    string curPath = recordingService.CurrentFilePath ?? string.Empty;
                    logger.LogInformation("[MEDIA] Current recording file path = {Path}", curPath);
                    bool fileExistsBefore = !string.IsNullOrEmpty(curPath) && File.Exists(curPath);
                    logger.LogInformation("[MEDIA] File.Exists = {Exists}", fileExistsBefore);
                    logger.LogInformation("[MEDIA] File length = {Length}", fileExistsBefore ? new FileInfo(curPath).Length : 0);

                    string recDir = AudioRecordingService.GetCanonicalRecordingsDirectory();
                    bool dirExists = Directory.Exists(recDir);
                    logger.LogInformation("[MEDIA] Directory.Exists = {Exists}", dirExists);
                    if (dirExists)
                    {
                        string[] contents = Directory.GetFiles(recDir);
                        logger.LogInformation("[MEDIA] Directory contents = {Contents}", string.Join(", ", contents));
                    }

                    recordingService.StopRecording();

                    string filePath = recordingService.CurrentFilePath ?? string.Empty;
                    string fileName = !string.IsNullOrEmpty(filePath) ? Path.GetFileName(filePath) : string.Empty;
                    long fileSize = (!string.IsNullOrEmpty(filePath) && File.Exists(filePath)) ? new FileInfo(filePath).Length : 0;

                    logger.LogInformation("[MEDIA] FINAL WAV PATH = {Path}", filePath);
                    logger.LogInformation("[MEDIA] FINAL WAV EXISTS = {Exists}", File.Exists(filePath));
                    logger.LogInformation("[MEDIA] FINAL WAV SIZE = {Size}", fileSize);

                    if (fileSize == 0 || string.IsNullOrEmpty(fileName))
                    {
                        resp.StatusCode = 500;
                        string errJson = $"{{\"status\":\"error\",\"message\":\"No recording file produced or file is 0 bytes\",\"fileName\":\"{fileName}\",\"fileSize\":0}}";
                        byte[] errBuf = Encoding.UTF8.GetBytes(errJson);
                        resp.ContentType = "application/json";
                        resp.OutputStream.Write(errBuf, 0, errBuf.Length);
                        resp.Close();
                        return;
                    }

                    string json = $"{{\"status\":\"stopped\",\"filePath\":\"{filePath.Replace("\\", "\\\\")}\",\"fileName\":\"{fileName}\",\"fileSize\":{fileSize}}}";
                    byte[] buf = Encoding.UTF8.GetBytes(json);
                    resp.ContentType = "application/json";
                    resp.OutputStream.Write(buf, 0, buf.Length);
                    resp.Close();
                    return;
                }

                if (path == "/api/media/recordings/list")
                {
                    string directory = req.QueryString["directory"] ?? "recordings";
                    string fullDir = Path.IsPathRooted(directory) ? directory : Path.Combine(AppDomain.CurrentDomain.BaseDirectory, directory);
                    var filesList = new System.Collections.Generic.List<object>();
                    if (Directory.Exists(fullDir))
                    {
                        foreach (var ext in new[] { "*.mp4", "*.wav" })
                        {
                            foreach (var f in Directory.GetFiles(fullDir, ext))
                            {
                                var fi = new FileInfo(f);
                                string format = fi.Extension.Equals(".mp4", StringComparison.OrdinalIgnoreCase) ? "video/mp4" : "audio/wav";
                                filesList.Add(new { name = fi.Name, sizeBytes = fi.Length, format = format, createdAt = fi.CreationTimeUtc.ToString("o") });
                            }
                        }
                    }
                    string json = Newtonsoft.Json.JsonConvert.SerializeObject(filesList);
                    byte[] buf = Encoding.UTF8.GetBytes(json);
                    resp.ContentType = "application/json";
                    resp.OutputStream.Write(buf, 0, buf.Length);
                    resp.Close();
                    return;
                }

                if (path == "/api/media/recordings/download")
                {
                    string fileName = req.QueryString["file"];
                    if (string.IsNullOrEmpty(fileName))
                    {
                        resp.StatusCode = 400;
                        byte[] errBuf = Encoding.UTF8.GetBytes("{\"error\":\"Missing 'file' query parameter\"}");
                        resp.OutputStream.Write(errBuf, 0, errBuf.Length);
                        resp.Close();
                        return;
                    }

                    string fullDir = AudioRecordingService.GetCanonicalRecordingsDirectory();
                    string targetFile = Path.Combine(fullDir, Path.GetFileName(fileName));

                    if (!File.Exists(targetFile))
                    {
                        string cwdFile = Path.Combine(Directory.GetCurrentDirectory(), "recordings", Path.GetFileName(fileName));
                        if (File.Exists(cwdFile))
                        {
                            targetFile = cwdFile;
                        }
                        else
                        {
                            string rootFile = Path.Combine(Directory.GetCurrentDirectory(), Path.GetFileName(fileName));
                            if (File.Exists(rootFile))
                            {
                                targetFile = rootFile;
                            }
                        }
                    }

                    bool targetExists = File.Exists(targetFile);
                    long targetLength = targetExists ? new FileInfo(targetFile).Length : 0;

                    logger.LogInformation("[MEDIA] DOWNLOAD requested file = {File}", fileName);
                    logger.LogInformation("[MEDIA] Resolved full path = {Path}", targetFile);
                    logger.LogInformation("[MEDIA] File.Exists = {Exists}", targetExists);
                    logger.LogInformation("[MEDIA] File size = {Size}", targetLength);
                    logger.LogInformation("[MEDIA] Base directory = {BaseDir}", AppDomain.CurrentDomain.BaseDirectory);
                    logger.LogInformation("[MEDIA] Recordings directory = {RecDir}", fullDir);

                    if (!targetExists || targetLength == 0)
                    {
                        resp.StatusCode = 404;
                        byte[] errBuf = Encoding.UTF8.GetBytes("{\"error\":\"File not found or 0 bytes\"}");
                        resp.OutputStream.Write(errBuf, 0, errBuf.Length);
                        resp.Close();
                        return;
                    }

                    byte[] fileBytes = File.ReadAllBytes(targetFile);
                    resp.ContentType = targetFile.EndsWith(".mp4", StringComparison.OrdinalIgnoreCase) ? "video/mp4" : "audio/wav";
                    resp.AddHeader("Content-Disposition", $"attachment; filename=\"{Path.GetFileName(targetFile)}\"");
                    resp.ContentLength64 = fileBytes.Length;
                    resp.OutputStream.Write(fileBytes, 0, fileBytes.Length);
                    resp.Close();
                    return;
                }


                resp.StatusCode = 404;
                byte[] err = Encoding.UTF8.GetBytes("{\"error\":\"Not Found\"}");
                resp.OutputStream.Write(err, 0, err.Length);
                resp.Close();
            }
            catch (Exception ex)
            {
                logger.LogError(ex, "Error processing HTTP request.");
            }
        }

        private static void LoadDotEnv()
        {
            try
            {
                string? current = Directory.GetCurrentDirectory();
                string? envPath = null;
                for (int i = 0; i < 5 && !string.IsNullOrEmpty(current); i++)
                {
                    string candidate = Path.Combine(current, ".env");
                    if (File.Exists(candidate))
                    {
                        envPath = candidate;
                        break;
                    }
                    current = Directory.GetParent(current)?.FullName;
                }

                if (string.IsNullOrEmpty(envPath) || !File.Exists(envPath))
                {
                    string baseDirEnv = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, ".env");
                    if (File.Exists(baseDirEnv)) envPath = baseDirEnv;
                }

                if (!string.IsNullOrEmpty(envPath) && File.Exists(envPath))
                {
                    foreach (var line in File.ReadAllLines(envPath))
                    {
                        var trimmed = line.Trim();
                        if (string.IsNullOrEmpty(trimmed) || trimmed.StartsWith("#")) continue;
                        var parts = trimmed.Split(new[] { '=' }, 2);
                        if (parts.Length == 2)
                        {
                            var key = parts[0].Trim();
                            var val = parts[1].Trim().Trim('"', '\'');
                            Environment.SetEnvironmentVariable(key, val);
                        }
                    }
                }
            }
            catch { }
        }
    }

    internal class TekMeetServiceBridge : ServiceBase
    {
        private readonly IHost _host;

        public TekMeetServiceBridge(IHost host)
        {
            _host = host;
            ServiceName = "TekMeetMediaWorker";
        }

        protected override void OnStart(string[] args)
        {
            _host.StartAsync().GetAwaiter().GetResult();
        }

        protected override void OnStop()
        {
            _host.StopAsync().GetAwaiter().GetResult();
        }
    }
}
