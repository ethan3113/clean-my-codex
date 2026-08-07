using System.ComponentModel;
using System.Diagnostics;
using System.Windows;
using Microsoft.Web.WebView2.Core;

namespace CleanMyCodexApp;

public partial class MainWindow : Window
{
    private ServerSession? _session;
    private readonly CancellationTokenSource _startupCancellation = new();
    private Task? _startupTask;
    private bool _closeApproved;
    private bool _closing;

    public MainWindow()
    {
        InitializeComponent();
        Loaded += OnLoaded;
        Closing += OnClosing;
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        _startupTask = InitializeAsync(_startupCancellation.Token);
        try
        {
            await _startupTask;
        }
        catch (OperationCanceledException) when (_closing)
        {
            // The close path owns service cleanup once startup is cancelled.
        }
        catch (WebView2RuntimeNotFoundException)
        {
            if (!_closing) PresentFatalError(
                "Microsoft Edge WebView2 Runtime is required. Install the Evergreen Runtime from Microsoft and reopen Clean My Codex."
            );
        }
        catch (Exception exception)
        {
            if (!_closing) PresentFatalError(exception.Message);
        }
    }

    private async Task InitializeAsync(CancellationToken cancellationToken)
    {
        var session = await ServerSession.StartAsync(cancellationToken);
        if (cancellationToken.IsCancellationRequested)
        {
            session.AbortFailedStart();
            session.CleanupSessionDirectory();
            cancellationToken.ThrowIfCancellationRequested();
        }
        _session = session;
        StartupLabel.Text = "Preparing the protected interface...";
        var webViewData = Path.Combine(session.SessionDirectory, "WebView2");
        var environment = await CoreWebView2Environment.CreateAsync(userDataFolder: webViewData);
        await Browser.EnsureCoreWebView2Async(environment);
        cancellationToken.ThrowIfCancellationRequested();
        ConfigureBrowser(Browser.CoreWebView2);
        Browser.Source = new Uri(
            $"{session.BaseUrl}/#token={Uri.EscapeDataString(session.Token)}"
        );
        Browser.Visibility = Visibility.Visible;
        StartupPanel.Visibility = Visibility.Collapsed;
    }

    private void ConfigureBrowser(CoreWebView2 core)
    {
        core.Settings.AreDevToolsEnabled = false;
        core.Settings.AreDefaultContextMenusEnabled = false;
        core.Settings.IsStatusBarEnabled = false;
        core.Settings.IsPasswordAutosaveEnabled = false;
        core.Settings.IsGeneralAutofillEnabled = false;
        core.NavigationStarting += (_, args) =>
        {
            if (!Uri.TryCreate(args.Uri, UriKind.Absolute, out var uri))
            {
                args.Cancel = true;
                return;
            }
            if (IsAllowedLocalUri(uri)) return;
            args.Cancel = true;
            if (IsAllowedExternalUri(uri)) OpenExternal(uri);
        };
        core.NewWindowRequested += (_, args) =>
        {
            args.Handled = true;
            if (Uri.TryCreate(args.Uri, UriKind.Absolute, out var uri) && IsAllowedExternalUri(uri))
            {
                OpenExternal(uri);
            }
        };
        core.PermissionRequested += (_, args) => args.State = CoreWebView2PermissionState.Deny;
        core.DownloadStarting += (_, args) => args.Cancel = true;
    }

    private bool IsAllowedLocalUri(Uri uri)
    {
        if (_session is null || !Uri.TryCreate(_session.BaseUrl, UriKind.Absolute, out var allowed))
        {
            return false;
        }
        return uri.Scheme == Uri.UriSchemeHttp &&
               uri.Host == "127.0.0.1" &&
               uri.Port == allowed.Port;
    }

    private static bool IsAllowedExternalUri(Uri uri)
    {
        if (uri.Scheme != Uri.UriSchemeHttps ||
            !uri.Host.Equals("github.com", StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }
        var path = uri.AbsolutePath.ToLowerInvariant();
        return path is "/ethan3113" or "/ethan3113/" or "/ethan3113/clean-my-codex" ||
               path.StartsWith("/ethan3113/clean-my-codex/", StringComparison.Ordinal);
    }

    private static void OpenExternal(Uri uri)
    {
        Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
    }

    private async void OnClosing(object? sender, CancelEventArgs e)
    {
        if (_closeApproved)
        {
            Application.Current.Shutdown();
            return;
        }

        e.Cancel = true;
        if (_closing) return;
        _closing = true;
        StartupLabel.Text = "Closing after the current operation is safe...";
        StartupPanel.Visibility = Visibility.Visible;
        StartupPanel.IsHitTestVisible = true;

        _startupCancellation.Cancel();
        if (_startupTask is not null)
        {
            try
            {
                await _startupTask;
            }
            catch
            {
                // Startup failure is handled below through the owned session state.
            }
        }

        if (_session is null)
        {
            _closeApproved = true;
            Close();
            return;
        }

        if (await _session.ShutdownAsync())
        {
            Browser.Dispose();
            _session.CleanupSessionDirectory();
            _session = null;
            _closeApproved = true;
            Close();
            return;
        }

        _closing = false;
        StartupPanel.Visibility = Visibility.Collapsed;
        MessageBox.Show(
            this,
            "Clean My Codex could not verify a safe shutdown. The window will remain open so no active operation is interrupted.",
            "Unable to close safely",
            MessageBoxButton.OK,
            MessageBoxImage.Warning
        );
    }

    private void PresentFatalError(string message)
    {
        _session?.AbortFailedStart();
        _session?.CleanupSessionDirectory();
        _session = null;
        StartupLabel.Text = "Clean My Codex could not start.";
        MessageBox.Show(this, message, "Clean My Codex", MessageBoxButton.OK, MessageBoxImage.Error);
        _closeApproved = true;
        Close();
    }
}
