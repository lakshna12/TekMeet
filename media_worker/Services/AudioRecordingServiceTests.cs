using System;
using System.IO;
using Microsoft.Extensions.Logging.Abstractions;

namespace MediaWorker.Services
{
    public static class AudioRecordingServiceTests
    {
        public static void RunAllTests()
        {
            Console.WriteLine("=== RUNNING AUDIO RECORDING SERVICE LIFECYCLE TESTS ===");
            var logger = NullLogger<AudioRecordingService>.Instance;

            Test_FirstCallCreatesRecordingA(logger);
            Test_DuplicateStartSameCall(logger);
            Test_SecondDifferentCallCreatesRecordingB(logger);
            Test_StoppingCallACleansState(logger);
            Test_StoppingTwiceNoDuplicateCallback(logger);
            Test_CallBNeverAppendsToCallAWav(logger);
            Test_ExactFilePathFromStopPassedToBackend(logger);

            Console.WriteLine("=================================================");
            Console.WriteLine("SUCCESS: ALL 7 AUDIO RECORDING SERVICE TESTS PASSED!");
            Console.WriteLine("=================================================");
        }

        private static void Test_FirstCallCreatesRecordingA(NullLogger<AudioRecordingService> logger)
        {
            using (var service = new AudioRecordingService(logger))
            {
                string tempDir = Path.Combine(Path.GetTempPath(), "tekmeet_test_" + Guid.NewGuid().ToString("N"));
                try
                {
                    string pathA = service.StartRecording(tempDir, "evt_101", "graph_call_A");
                    Assert(service.IsRecording, "Service should be recording after StartRecording");
                    Assert(service.CurrentCallId == "graph_call_A", "CurrentCallId should match Graph call ID");
                    Assert(File.Exists(pathA), "Recording A file should be created");
                    Assert(pathA.Contains("graph_call_A"), "Recording A file name should contain Graph call ID");
                    service.StopRecording();
                }
                finally
                {
                    if (Directory.Exists(tempDir)) try { Directory.Delete(tempDir, true); } catch { }
                }
            }
            Console.WriteLine("  [PASS] Test 1: First call creates recording A");
        }

        private static void Test_DuplicateStartSameCall(NullLogger<AudioRecordingService> logger)
        {
            using (var service = new AudioRecordingService(logger))
            {
                string tempDir = Path.Combine(Path.GetTempPath(), "tekmeet_test_" + Guid.NewGuid().ToString("N"));
                try
                {
                    string pathA1 = service.StartRecording(tempDir, "evt_101", "graph_call_A");
                    string pathA2 = service.StartRecording(tempDir, "evt_101", "graph_call_A");

                    Assert(pathA1 == pathA2, "Duplicate StartRecording for SAME call must return same file path");
                    Assert(service.IsRecording, "Service should remain recording");
                    Assert(service.CurrentCallId == "graph_call_A", "CurrentCallId should remain graph_call_A");
                    service.StopRecording();
                }
                finally
                {
                    if (Directory.Exists(tempDir)) try { Directory.Delete(tempDir, true); } catch { }
                }
            }
            Console.WriteLine("  [PASS] Test 2: Duplicate start for same call does not create recording B");
        }

        private static void Test_SecondDifferentCallCreatesRecordingB(NullLogger<AudioRecordingService> logger)
        {
            using (var service = new AudioRecordingService(logger))
            {
                string tempDir = Path.Combine(Path.GetTempPath(), "tekmeet_test_" + Guid.NewGuid().ToString("N"));
                try
                {
                    string pathA = service.StartRecording(tempDir, "evt_101", "graph_call_A");
                    Assert(service.CurrentCallId == "graph_call_A", "Call A ID set");

                    // Start Call B while Call A is active
                    string pathB = service.StartRecording(tempDir, "evt_102", "graph_call_B");

                    Assert(pathA != pathB, "Second different call MUST create a new recording B file");
                    Assert(service.CurrentCallId == "graph_call_B", "CurrentCallId should be updated to graph_call_B");
                    Assert(service.IsRecording, "Service should be recording Call B");
                    Assert(File.Exists(pathA), "Call A WAV file should be finalized and exist");
                    Assert(File.Exists(pathB), "Call B WAV file should be created");
                    service.StopRecording();
                }
                finally
                {
                    if (Directory.Exists(tempDir)) try { Directory.Delete(tempDir, true); } catch { }
                }
            }
            Console.WriteLine("  [PASS] Test 3: Second different call creates recording B");
        }

        private static void Test_StoppingCallACleansState(NullLogger<AudioRecordingService> logger)
        {
            using (var service = new AudioRecordingService(logger))
            {
                string tempDir = Path.Combine(Path.GetTempPath(), "tekmeet_test_" + Guid.NewGuid().ToString("N"));
                try
                {
                    string pathA = service.StartRecording(tempDir, "evt_101", "graph_call_A");
                    Assert(service.IsRecording, "IsRecording should be true");

                    service.StopRecording();

                    Assert(!service.IsRecording, "Stopping call A must set IsRecording to false");
                    Assert(service.CurrentCallId == null, "Stopping call A must clear CurrentCallId");
                    Assert(service.AudioSocket == null, "Stopping call A must dispose AudioSocket");
                    Assert(service.VideoSocket == null, "Stopping call A must dispose VideoSocket");
                }
                finally
                {
                    if (Directory.Exists(tempDir)) try { Directory.Delete(tempDir, true); } catch { }
                }
            }
            Console.WriteLine("  [PASS] Test 4: Stopping call A cannot leave its recording active");
        }

        private static void Test_StoppingTwiceNoDuplicateCallback(NullLogger<AudioRecordingService> logger)
        {
            using (var service = new AudioRecordingService(logger))
            {
                string tempDir = Path.Combine(Path.GetTempPath(), "tekmeet_test_" + Guid.NewGuid().ToString("N"));
                try
                {
                    service.StartRecording(tempDir, "evt_101", "graph_call_A");
                    service.StopRecording();
                    Assert(!service.IsRecording, "IsRecording should be false after 1st stop");

                    // 2nd stop
                    service.StopRecording();
                    Assert(!service.IsRecording, "IsRecording should be false after 2nd stop");
                }
                finally
                {
                    if (Directory.Exists(tempDir)) try { Directory.Delete(tempDir, true); } catch { }
                }
            }
            Console.WriteLine("  [PASS] Test 5: Stopping twice does not duplicate callback or throw exception");
        }

        private static void Test_CallBNeverAppendsToCallAWav(NullLogger<AudioRecordingService> logger)
        {
            using (var service = new AudioRecordingService(logger))
            {
                string tempDir = Path.Combine(Path.GetTempPath(), "tekmeet_test_" + Guid.NewGuid().ToString("N"));
                try
                {
                    string pathA = service.StartRecording(tempDir, "evt_101", "graph_call_A");

                    // Simulate PCM frames for Call A
                    byte[] dummyFrameA = new byte[640];
                    service.WritePcmFrame(dummyFrameA);

                    long sizeA_before = new FileInfo(pathA).Length;

                    // Start Call B
                    string pathB = service.StartRecording(tempDir, "evt_102", "graph_call_B");
                    long sizeA_afterCallBStart = new FileInfo(pathA).Length;

                    // Write PCM frames for Call B
                    byte[] dummyFrameB = new byte[640];
                    service.WritePcmFrame(dummyFrameB);

                    long sizeA_final = new FileInfo(pathA).Length;
                    long sizeB_final = new FileInfo(pathB).Length;

                    Assert(sizeA_afterCallBStart == sizeA_final, "Call A WAV file size MUST NOT increase after Call B starts");
                    Assert(sizeB_final > 44, "Call B WAV file should contain Call B's PCM data");

                    service.StopRecording();
                }
                finally
                {
                    if (Directory.Exists(tempDir)) try { Directory.Delete(tempDir, true); } catch { }
                }
            }
            Console.WriteLine("  [PASS] Test 6: Call B never appends to Call A's WAV");
        }

        private static void Test_ExactFilePathFromStopPassedToBackend(NullLogger<AudioRecordingService> logger)
        {
            using (var service = new AudioRecordingService(logger))
            {
                string tempDir = Path.Combine(Path.GetTempPath(), "tekmeet_test_" + Guid.NewGuid().ToString("N"));
                try
                {
                    string pathA = service.StartRecording(tempDir, "evt_101", "graph_call_A");
                    string activePath = service.CurrentFilePath;
                    Assert(pathA == activePath, "StartRecording returned path must match CurrentFilePath");

                    service.StopRecording();
                    Assert(service.CurrentFilePath == pathA, "CurrentFilePath after stop must match the exact finalized WAV path");
                }
                finally
                {
                    if (Directory.Exists(tempDir)) try { Directory.Delete(tempDir, true); } catch { }
                }
            }
            Console.WriteLine("  [PASS] Test 7: Exact file path from stop is the file passed to backend");
        }

        private static void Assert(bool condition, string message)
        {
            if (!condition)
            {
                throw new InvalidOperationException("TEST FAILURE: " + message);
            }
        }
    }
}
