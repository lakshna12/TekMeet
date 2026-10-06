using System;
using System.Net;
using System.Reflection;
using System.Security.Cryptography.X509Certificates;
using System.Text.RegularExpressions;
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
        public int PublicPort { get; set; } = 8445;
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

        public async Task StartAsync(CancellationToken cancellationToken)
        {
            _logger.LogInformation("=== TEKMEET MEDIA WORKER: INITIALIZING MEDIA PLATFORM ===");
            _logger.LogInformation("Configuration Structure:");
            _logger.LogInformation("  - Public IP          : {PublicIp}", _options.PublicIp ?? "(Not set - pending deployment)");
            _logger.LogInformation("  - Media FQDN         : {MediaFqdn}", _options.MediaFqdn ?? "(Not set - pending deployment)");
            _logger.LogInformation("  - Internal Port      : {InternalPort}", _options.InternalPort);
            _logger.LogInformation("  - Public Port        : {PublicPort}", _options.PublicPort);
            _logger.LogInformation("  - UDP Port Range     : {Start} - {End}", _options.MediaUdpPortStart, _options.MediaUdpPortEnd);

            var rawThumbprint = _options.CertificateThumbprint ?? string.Empty;
            var cleanThumbprint = Regex.Replace(rawThumbprint, @"[^a-fA-F0-9]", "").ToUpperInvariant();
            _logger.LogInformation("  - Cert Thumbprint    : {Thumbprint} (Sanitized from: {Raw})", cleanThumbprint, rawThumbprint);

            var maskedAppId = string.IsNullOrEmpty(_options.AppId)
                ? "(Not set)"
                : (_options.AppId.Length > 8 ? _options.AppId.Substring(0, 8) + "..." : _options.AppId);
            _logger.LogInformation("  - App ID             : {AppId}", maskedAppId);

            InspectCertificateStore(cleanThumbprint);

            if (!string.IsNullOrWhiteSpace(_options.PublicIp) &&
                !string.IsNullOrWhiteSpace(_options.MediaFqdn) &&
                !string.IsNullOrWhiteSpace(_options.AppId))
            {
                try
                {
                    var instanceSettings = new MediaPlatformInstanceSettings
                    {
                        CertificateThumbprint = cleanThumbprint,
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

                    _logger.LogInformation("[MediaPlatform] Calling MediaPlatform.Shutdown() prior to initialization...");
                    try
                    {
                        MediaPlatform.Shutdown();
                    }
                    catch { }

                    await Task.Delay(500, cancellationToken);

                    _logger.LogInformation("[MediaPlatform] Invoking MediaPlatform.Initialize(settings)...");
                    MediaPlatform.Initialize(settings);
                    _isInitialized = true;
                    _logger.LogInformation("SUCCESS: Microsoft Skype/Graph Real-Time Media Platform initialized successfully. IsInitialized=True");
                }
                catch (Exception ex)
                {
                    _isInitialized = false;
                    _logger.LogError(ex, "ERROR: Failed to initialize Microsoft Real-Time Media Platform.\n" +
                                         "  - Message: {Message}\n" +
                                         "  - Type: {Type}\n" +
                                         "  - InnerException: {Inner}",
                                         ex.Message,
                                         ex.GetType().FullName,
                                         ex.InnerException != null ? $"{ex.InnerException.GetType().FullName}: {ex.InnerException.Message}" : "None");
                }
            }
            else
            {
                _logger.LogWarning("NOTICE: Real-Time Media Platform settings (PUBLIC_IP, MEDIA_FQDN) are incomplete. Platform initialization deferred.");
            }
        }

        private void InspectCertificateStore(string targetThumbprint)
        {
            _logger.LogInformation("=== [CERT CHECK] Inspecting Windows Certificate Stores ===");
            bool matchFound = false;

            foreach (var location in new[] { StoreLocation.LocalMachine, StoreLocation.CurrentUser })
            {
                try
                {
                    using (var store = new X509Store(StoreName.My, location))
                    {
                        store.Open(OpenFlags.ReadOnly);
                        var certs = store.Certificates;
                        _logger.LogInformation("[CERT CHECK] StoreLocation.{Location} contains {Count} certificates.", location, certs.Count);

                        foreach (var cert in certs)
                        {
                            string certThumb = cert.Thumbprint?.Replace(" ", "").ToUpperInvariant() ?? "";
                            bool isMatch = string.Equals(certThumb, targetThumbprint, StringComparison.OrdinalIgnoreCase);
                            if (isMatch)
                            {
                                matchFound = true;
                                _logger.LogInformation("[CERT CHECK] MATCH FOUND in StoreLocation.{Location}!\n" +
                                                       "  - Subject       : {Subject}\n" +
                                                       "  - Thumbprint    : {Thumb}\n" +
                                                       "  - HasPrivateKey : {HasKey}\n" +
                                                       "  - NotAfter      : {Expiration}",
                                                       location, cert.Subject, certThumb, cert.HasPrivateKey, cert.NotAfter.ToString("o"));
                            }
                        }
                    }
                }
                catch (Exception ex)
                {
                    _logger.LogWarning(ex, "[CERT CHECK] Could not inspect StoreLocation.{Location}", location);
                }
            }

            if (!matchFound)
            {
                _logger.LogWarning("[CERT CHECK] WARNING: Certificate with thumbprint '{TargetThumb}' was NOT found in LocalMachine\\My or CurrentUser\\My stores.", targetThumbprint);
            }
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
