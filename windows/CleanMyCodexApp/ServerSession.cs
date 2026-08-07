using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace CleanMyCodexApp;

internal sealed class ServerSession
{
    private const string SessionHeader = "X-Clean-My-Codex-Token";
    private const string ShutdownPath = "/api/lifecycle/shutdown";
    private readonly Process _server;
    private readonly SemaphoreSlim _shutdownLock = new(1, 1);
    private bool _safeShutdownCompleted;

    private ServerSession(Process server, string sessionDirectory, string token, string baseUrl)
    {
        _server = server;
        SessionDirectory = sessionDirectory;
        Token = token;
        BaseUrl = baseUrl;
    }

    public string SessionDirectory { get; }
    public string Token { get; }
    public string BaseUrl { get; }

    public static async Task<ServerSession> StartAsync(CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var bundleRoot = AppContext.BaseDirectory;
        var serverPath = Path.Combine(bundleRoot, "server", "clean-my-codex-server.exe");
        var staticDirectory = Path.Combine(bundleRoot, "app", "static");
        if (!File.Exists(serverPath)) throw new InvalidOperationException("The application service is missing.");
        if (!File.Exists(Path.Combine(staticDirectory, "index.html")))
        {
            throw new InvalidOperationException("The application interface is missing.");
        }

        var profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var localData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        var codexHome = ResolveAbsoluteOverride("CLEAN_MY_CODEX_HOME", Path.Combine(profile, ".codex"));
        var appHome = ResolveAbsoluteOverride("CLEAN_MY_CODEX_APP_HOME", Path.Combine(localData, "Clean My Codex"));
        Directory.CreateDirectory(appHome);
        var runtimeRoot = Path.Combine(appHome, "Runtime");
        Directory.CreateDirectory(runtimeRoot);
        var sessionDirectory = Path.Combine(runtimeRoot, Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(sessionDirectory);

        var token = Base64Url(RandomNumberGenerator.GetBytes(32));
        var tokenFile = Path.Combine(sessionDirectory, "session-token");
        var readyFile = Path.Combine(sessionDirectory, "ready.json");
        using (var stream = new FileStream(tokenFile, FileMode.CreateNew, FileAccess.Write, FileShare.None))
        {
            var bytes = Encoding.UTF8.GetBytes(token + Environment.NewLine);
            await stream.WriteAsync(bytes, cancellationToken);
            await stream.FlushAsync(cancellationToken);
        }

        var start = new ProcessStartInfo(serverPath)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            WorkingDirectory = bundleRoot,
        };
        foreach (var argument in new[]
                 {
                     "--host", "127.0.0.1", "--port", "0",
                     "--codex-home", codexHome,
                     "--app-home", appHome,
                     "--static-dir", staticDirectory,
                     "--token-file", tokenFile,
                     "--ready-file", readyFile,
                 })
        {
            start.ArgumentList.Add(argument);
        }

        var server = Process.Start(start) ?? throw new InvalidOperationException("The application service could not start.");
        server.BeginOutputReadLine();
        server.BeginErrorReadLine();
        try
        {
            var port = await WaitForReadyPortAsync(
                server, readyFile, TimeSpan.FromSeconds(20), cancellationToken
            );
            var session = new ServerSession(server, sessionDirectory, token, $"http://127.0.0.1:{port}");
            await session.VerifyHealthAsync(cancellationToken);
            cancellationToken.ThrowIfCancellationRequested();
            return session;
        }
        catch
        {
            try
            {
                if (!server.HasExited)
                {
                    server.Kill(entireProcessTree: true);
                    await server.WaitForExitAsync();
                }
            }
            catch (InvalidOperationException) { }
            TryDeleteDirectory(sessionDirectory);
            throw;
        }
    }

    public async Task<bool> ShutdownAsync()
    {
        await _shutdownLock.WaitAsync();
        try
        {
            if (_safeShutdownCompleted || _server.HasExited) return true;
            using var client = new HttpClient { Timeout = Timeout.InfiniteTimeSpan };
            using var request = new HttpRequestMessage(HttpMethod.Post, $"{BaseUrl}{ShutdownPath}");
            request.Headers.Add(SessionHeader, Token);
            request.Content = new StringContent("{}", Encoding.UTF8, "application/json");
            using var response = await client.SendAsync(request);
            if (!response.IsSuccessStatusCode) return false;
            using var payload = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
            if (!payload.RootElement.TryGetProperty("safe_to_terminate", out var safe) || !safe.GetBoolean())
            {
                return false;
            }

            _safeShutdownCompleted = true;
            if (!_server.HasExited)
            {
                _server.Kill(entireProcessTree: true);
                await _server.WaitForExitAsync();
            }
            return true;
        }
        catch
        {
            return _server.HasExited;
        }
        finally
        {
            _shutdownLock.Release();
        }
    }

    public void AbortFailedStart()
    {
        try
        {
            if (_server.HasExited) return;
            _server.Kill(entireProcessTree: true);
            _server.WaitForExit(5000);
        }
        catch (InvalidOperationException) { }
    }

    public void CleanupSessionDirectory() => TryDeleteDirectory(SessionDirectory);

    private async Task VerifyHealthAsync(CancellationToken cancellationToken)
    {
        using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(10) };
        using var request = new HttpRequestMessage(HttpMethod.Get, $"{BaseUrl}/api/health");
        request.Headers.Add(SessionHeader, Token);
        using var response = await client.SendAsync(request, cancellationToken);
        response.EnsureSuccessStatusCode();
        using var payload = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
        if (!payload.RootElement.TryGetProperty("ok", out var ok) || !ok.GetBoolean())
        {
            throw new InvalidOperationException("The application service did not pass its health check.");
        }
    }

    private static async Task<int> WaitForReadyPortAsync(
        Process server,
        string readyFile,
        TimeSpan timeout,
        CancellationToken cancellationToken
    )
    {
        var deadline = Stopwatch.StartNew();
        while (deadline.Elapsed < timeout)
        {
            if (server.HasExited) throw new InvalidOperationException("The application service stopped during startup.");
            if (File.Exists(readyFile))
            {
                if ((File.GetAttributes(readyFile) & FileAttributes.ReparsePoint) != 0)
                {
                    throw new InvalidOperationException("The protected application session is unsafe.");
                }
                using var payload = JsonDocument.Parse(await File.ReadAllTextAsync(readyFile));
                if (payload.RootElement.TryGetProperty("port", out var port) &&
                    port.TryGetInt32(out var value) && value is >= 1 and <= 65535)
                {
                    return value;
                }
                throw new InvalidOperationException("The protected application address is invalid.");
            }
            await Task.Delay(100, cancellationToken);
        }
        throw new TimeoutException("The application service did not become ready in time.");
    }

    private static string ResolveAbsoluteOverride(string name, string fallback)
    {
        var value = Environment.GetEnvironmentVariable(name);
        var selected = string.IsNullOrWhiteSpace(value) ? fallback : value;
        if (!Path.IsPathFullyQualified(selected))
        {
            throw new InvalidOperationException($"{name} must be an absolute path.");
        }
        return Path.GetFullPath(selected);
    }

    private static string Base64Url(byte[] value) => Convert.ToBase64String(value)
        .TrimEnd('=')
        .Replace('+', '-')
        .Replace('/', '_');

    private static void TryDeleteDirectory(string path)
    {
        try
        {
            if (Directory.Exists(path)) Directory.Delete(path, recursive: true);
        }
        catch
        {
            // Windows may briefly retain WebView2 files; the next launch is unaffected.
        }
    }
}

internal static class DesktopSmokeTest
{
    public static async Task<int> RunAsync()
    {
        ServerSession? session = null;
        try
        {
            session = await ServerSession.StartAsync();
            var stopped = await session.ShutdownAsync();
            session.CleanupSessionDirectory();
            return stopped ? 0 : 1;
        }
        catch
        {
            session?.AbortFailedStart();
            session?.CleanupSessionDirectory();
            return 1;
        }
    }
}
