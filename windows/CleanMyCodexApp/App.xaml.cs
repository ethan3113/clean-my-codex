using System.Windows;

namespace CleanMyCodexApp;

public partial class App : Application
{
    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        if (e.Args.Contains("--smoke-test", StringComparer.OrdinalIgnoreCase))
        {
            Shutdown(await DesktopSmokeTest.RunAsync());
            return;
        }

        var window = new MainWindow();
        MainWindow = window;
        window.Show();
    }
}
