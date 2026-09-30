using System;
using System.Net;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Skype.Bots.Media;

namespace MediaWorker.Services
{
    public class MediaPlatformOptions
    {
        public string? PublicIp { get; set; }
        public string? MediaFqdn { get; set; }
        public int MediaUdpPortStart { get; set; } = 20000;
        public int MediaUdpPortEnd { get; set; } = 20050;
        public int InternalPort { get; set; } = 8445;
        public int PublicPort { get; set; } = 443;
        public string? AppId { get; set; }
        public string? CertificateThumbprint { get; set; }
    }

    public class MediaPlatformHost : IHostedService, IDisposable
    {
        private readonly ILogger<MediaPlatformHost> _logger;
        private readonly IConfiguration _configuration;
        private readonly AudioRecordingService _audioRecordingService;
        private readonly MediaPlatformOptions _options;
        private bool _isInitialized;

        public MediaPlatformHost(
            ILogger<MediaPlatformHost> logger,
            IConfiguration configuration,
            AudioRecordingService audioRecordingService)
        {
            _logger = logger;
            _configuration = configuration;
            _audioRecordingService = audioRecordingService;

            _options = new MediaPlatformOptions
            {
                PublicIp = _configuration["PUBLIC_IP"] ?? _configuration["MediaPlatform:PublicIp"] ?? "168.62.176.182",
                MediaFqdn = _configuration["MEDIA_FQDN"] ?? _configuration["MediaPlatform:MediaFqdn"] ?? "tekmeet-media-vm231.eastus.cloudapp.azure.com",
                AppId = _configuration["AZURE_CLIENT_ID"] ?? _configuration["AZURE_BOT_APP_ID"] ?? _configuration["MediaPlatform:AppId"] ?? "face2a7e-877f-4963-93d5-161d329cd8d9",
                CertificateThumbprint = _configuration["CERTIFICATE_THUMBPRINT"] ?? _configuration["MediaPlatform:CertificateThumbprint"] ?? "88D6F42FF4F7139D0A434E2CFAB43E3B05045AC4"
            };

            if (int.TryParse(_configuration["MEDIA_UDP_PORT_START"], out var pStart))
                _options.MediaUdpPortStart = pStart;

            if (int.TryParse(_configuration["MEDIA_UDP_PORT_END"], out var pEnd))
                _options.MediaUdpPortEnd = pEnd;

            if (int.TryParse(_configuration["MEDIA_INTERNAL_PORT"], out var pInt))
                _options.InternalPort = pInt;

            if (int.TryParse(_configuration["MEDIA_PUBLIC_PORT"], out var pPub))
                _options.PublicPort = pPub;
        }

        public bool IsInitialized => _isInitialized;
        public AudioRecordingService AudioRecordingService => _audioRecordingService;

        /// <summary>
        /// Generates the AppHostedMediaConfig blob using MediaPlatform.CreateMediaConfiguration(audioSocket).
        /// </summary>
        public string GetMediaConfigurationBlob()
        {
            if (!_isInitialized)
            {
                _logger.LogWarning("GetMediaConfigurationBlob called but MediaPlatform is not initialized (missing PUBLIC_IP or MEDIA_FQDN).");
                return string.Empty;
            }

            try
            {
                var (audioSocket, videoSocket) = _audioRecordingService.CreateSockets(forceNew: false);
                var config = MediaPlatform.CreateMediaConfiguration(audioSocket, videoSocket);
                if (config == null) return string.Empty;

                var blobProp = config.GetType().GetProperty("Blob", BindingFlags.Public | BindingFlags.Instance);
                if (blobProp != null)
                {
                    var val = blobProp.GetValue(config) as string;
                    if (!string.IsNullOrEmpty(val))
                        return val;
                }

                return config.ToString() ?? string.Empty;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Exception inside GetMediaConfigurationBlob");
                return string.Empty;
            }
        }

        public Task StartAsync(CancellationToken cancellationToken)
        {
            _logger.LogInformation("=== TEKMEET MEDIA WORKER: INITIALIZING MEDIA PLATFORM ===");
            _logger.LogInformation("Configuration Structure:");
            _logger.LogInformation("  - Public IP          : {PublicIp}", _options.PublicIp ?? "(Not set - pending deployment)");
            _logger.LogInformation("  - Media FQDN         : {MediaFqdn}", _options.MediaFqdn ?? "(Not set - pending deployment)");
            _logger.LogInformation("  - Internal Port      : {InternalPort}", _options.InternalPort);
            _logger.LogInformation("  - Public Port        : {PublicPort}", _options.PublicPort);
            _logger.LogInformation("  - UDP Port Range     : {Start} - {End}", _options.MediaUdpPortStart, _options.MediaUdpPortEnd);

            var maskedAppId = string.IsNullOrEmpty(_options.AppId)
                ? "(Not set)"
                : (_options.AppId.Length > 8 ? _options.AppId.Substring(0, 8) + "..." : _options.AppId);
            _logger.LogInformation("  - App ID             : {AppId}", maskedAppId);

            Task.Run(async () =>
            {
                try
                {
                    if (!string.IsNullOrWhiteSpace(_options.PublicIp) &&
                        !string.IsNullOrWhiteSpace(_options.MediaFqdn) &&
                        !string.IsNullOrWhiteSpace(_options.AppId))
                    {
                        var instanceSettings = new MediaPlatformInstanceSettings
                        {
                            CertificateThumbprint = _options.CertificateThumbprint ?? string.Empty,
                            InstanceInternalPort = _options.InternalPort,
                            InstancePublicPort = _options.PublicPort,
                            InstancePublicIPAddress = IPAddress.Parse(_options.PublicIp),
                            ServiceFqdn = _options.MediaFqdn
                        };

                        var settings = new MediaPlatformSettings
                        {
                            ApplicationId = _options.AppId,
                            MediaPlatformInstanceSettings = instanceSettings
                        };

                        try
                        {
                            MediaPlatform.Shutdown();
                        }
                        catch { }

                        await Task.Delay(1000);

                        MediaPlatform.Initialize(settings);
                        _isInitialized = true;
                        _logger.LogInformation("SUCCESS: Microsoft Skype/Graph Real-Time Media Platform initialized successfully.");
                    }
                    else
                    {
                        _logger.LogWarning("NOTICE: Real-Time Media Platform settings (PUBLIC_IP, MEDIA_FQDN) are incomplete. Platform initialization deferred to runtime configuration.");
                    }
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "ERROR: Failed to initialize Microsoft Real-Time Media Platform.");
                    _isInitialized = false;
                }
            });

            return Task.CompletedTask;
        }

        public Task StopAsync(CancellationToken cancellationToken)
        {
            _logger.LogInformation("Stopping Media Platform Host...");
            Dispose();
            return Task.CompletedTask;
        }

        public void Dispose()
        {
            if (_isInitialized)
            {
                try
                {
                    MediaPlatform.Shutdown();
                    _logger.LogInformation("Media Platform shut down cleanly.");
                }
                catch (Exception ex)
                {
                    _logger.LogWarning(ex, "Exception during Media Platform shutdown.");
                }
                finally
                {
                    _isInitialized = false;
                }
            }
        }
    }
}
