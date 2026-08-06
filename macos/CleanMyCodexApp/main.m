#import <Cocoa/Cocoa.h>
#import <Security/Security.h>
#import <WebKit/WebKit.h>
#import <signal.h>
#import <sys/stat.h>
#import <unistd.h>

static NSString *const CMCAppName = @"Clean My Codex";
static NSString *const CMCLoopbackHost = @"127.0.0.1";
static NSString *const CMCShutdownPath = @"/api/lifecycle/shutdown";
static NSString *const CMCSessionHeader = @"X-Clean-My-Codex-Token";

static NSError *CMCError(NSString *message) {
    return [NSError errorWithDomain:@"studio.envocs.cleanmycodex"
                               code:1
                           userInfo:@{NSLocalizedDescriptionKey: message}];
}

@interface CMCAppDelegate : NSObject <NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate>
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) WKWebView *webView;
@property(nonatomic, strong) NSTask *serverTask;
@property(nonatomic, strong) NSURL *sessionDirectory;
@property(nonatomic, copy) NSString *sessionToken;
@property(nonatomic) NSInteger allowedPort;
@property(nonatomic) BOOL isTerminating;
@property(nonatomic) BOOL didPresentFatalError;
@property(nonatomic) BOOL terminationPending;
@property(nonatomic) BOOL terminationApproved;
- (void)installMainMenu;
- (void)requestGracefulShutdown;
@end

@implementation CMCAppDelegate

- (void)installMainMenu {
    NSMenu *menu = [[NSMenu alloc] init];
    NSMenuItem *applicationItem = [[NSMenuItem alloc] init];
    NSMenu *applicationMenu = [[NSMenu alloc] init];

    NSMenuItem *aboutItem = [[NSMenuItem alloc]
        initWithTitle:[NSString stringWithFormat:@"About %@", CMCAppName]
               action:@selector(orderFrontStandardAboutPanel:)
        keyEquivalent:@""];
    aboutItem.target = NSApp;
    [applicationMenu addItem:aboutItem];
    [applicationMenu addItem:[NSMenuItem separatorItem]];

    NSMenuItem *quitItem = [[NSMenuItem alloc]
        initWithTitle:[NSString stringWithFormat:@"Quit %@", CMCAppName]
               action:@selector(terminate:)
        keyEquivalent:@"q"];
    quitItem.target = NSApp;
    [applicationMenu addItem:quitItem];

    applicationItem.submenu = applicationMenu;
    [menu addItem:applicationItem];
    NSApp.mainMenu = menu;
}

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    [self createWindow];
    NSError *error = nil;
    if (![self startServer:&error]) {
        [self presentFatalError:error.localizedDescription];
    }
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender {
    return YES;
}

- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
    BOOL serviceCanBeStoppedImmediately = self.serverTask == nil ||
                                           !self.serverTask.running ||
                                           self.allowedPort < 1 ||
                                           self.sessionToken.length == 0;
    if (self.terminationApproved || serviceCanBeStoppedImmediately) {
        return NSTerminateNow;
    }
    if (self.terminationPending) {
        return NSTerminateLater;
    }

    self.terminationPending = YES;
    self.isTerminating = YES;
    [self requestGracefulShutdown];
    return NSTerminateLater;
}

- (void)applicationWillTerminate:(NSNotification *)notification {
    self.isTerminating = YES;
    [self stopServer];
    [self removeSessionDirectory];
    self.sessionToken = nil;
}

- (BOOL)windowShouldClose:(NSWindow *)sender {
    if (self.terminationApproved) return YES;
    [NSApp terminate:nil];
    return NO;
}

- (void)createWindow {
    NSRect contentRect = NSMakeRect(0, 0, 1280, 820);
    NSWindowStyleMask style = NSWindowStyleMaskTitled |
                              NSWindowStyleMaskClosable |
                              NSWindowStyleMaskMiniaturizable |
                              NSWindowStyleMaskResizable;
    self.window = [[NSWindow alloc] initWithContentRect:contentRect
                                              styleMask:style
                                                backing:NSBackingStoreBuffered
                                                  defer:NO];
    self.window.title = CMCAppName;
    self.window.titleVisibility = NSWindowTitleHidden;
    self.window.titlebarAppearsTransparent = YES;
    self.window.appearance = [NSAppearance appearanceNamed:NSAppearanceNameDarkAqua];
    self.window.minSize = NSMakeSize(900, 620);
    self.window.backgroundColor = [NSColor colorWithCalibratedWhite:0.03 alpha:1];
    self.window.delegate = self;
    self.window.contentView = [self startupView];
    [self.window center];
    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
}

- (NSView *)startupView {
    NSView *view = [[NSView alloc] init];
    view.wantsLayer = YES;
    view.layer.backgroundColor = [NSColor colorWithCalibratedWhite:0.03 alpha:1].CGColor;

    NSProgressIndicator *progress = [[NSProgressIndicator alloc] init];
    progress.style = NSProgressIndicatorStyleSpinning;
    progress.controlSize = NSControlSizeSmall;
    progress.translatesAutoresizingMaskIntoConstraints = NO;
    [progress startAnimation:nil];

    NSTextField *label = [NSTextField labelWithString:[NSString stringWithFormat:@"Starting %@...", CMCAppName]];
    label.font = [NSFont systemFontOfSize:12 weight:NSFontWeightMedium];
    label.textColor = NSColor.secondaryLabelColor;
    label.translatesAutoresizingMaskIntoConstraints = NO;

    NSStackView *stack = [NSStackView stackViewWithViews:@[progress, label]];
    stack.orientation = NSUserInterfaceLayoutOrientationVertical;
    stack.alignment = NSLayoutAttributeCenterX;
    stack.spacing = 12;
    stack.translatesAutoresizingMaskIntoConstraints = NO;
    [view addSubview:stack];
    [NSLayoutConstraint activateConstraints:@[
        [stack.centerXAnchor constraintEqualToAnchor:view.centerXAnchor],
        [stack.centerYAnchor constraintEqualToAnchor:view.centerYAnchor],
    ]];
    return view;
}

- (BOOL)startServer:(NSError **)error {
    NSURL *resources = NSBundle.mainBundle.resourceURL;
    if (resources == nil) {
        if (error) *error = CMCError(@"The application bundle is missing its resources.");
        return NO;
    }
    NSURL *serverExecutable = [resources URLByAppendingPathComponent:@"server/clean-my-codex-server"];
    NSURL *staticDirectory = [resources URLByAppendingPathComponent:@"app/static" isDirectory:YES];
    NSURL *indexFile = [staticDirectory URLByAppendingPathComponent:@"index.html"];
    NSFileManager *manager = NSFileManager.defaultManager;
    if (![manager isExecutableFileAtPath:serverExecutable.path]) {
        if (error) *error = CMCError(@"The application bundle is missing its service.");
        return NO;
    }
    if (![manager fileExistsAtPath:indexFile.path]) {
        if (error) *error = CMCError(@"The application bundle is missing its interface files.");
        return NO;
    }

    NSURL *appSupport = [self applicationSupportDirectory:error];
    if (appSupport == nil) return NO;

    NSURL *sessionDirectory = [manager.temporaryDirectory
        URLByAppendingPathComponent:[NSString stringWithFormat:@"clean-my-codex-%@", NSUUID.UUID.UUIDString]
                        isDirectory:YES];
    if (![manager createDirectoryAtURL:sessionDirectory
            withIntermediateDirectories:NO
                             attributes:@{NSFilePosixPermissions: @0700}
                                  error:error]) {
        return NO;
    }
    chmod(sessionDirectory.fileSystemRepresentation, 0700);
    self.sessionDirectory = sessionDirectory;

    NSString *token = [self sessionToken:error];
    if (token == nil) return NO;
    self.sessionToken = token;
    NSURL *tokenFile = [sessionDirectory URLByAppendingPathComponent:@"session-token"];
    NSURL *readyFile = [sessionDirectory URLByAppendingPathComponent:@"ready.json"];
    NSData *tokenData = [[token stringByAppendingString:@"\n"] dataUsingEncoding:NSUTF8StringEncoding];
    if (![tokenData writeToURL:tokenFile options:NSDataWritingAtomic error:error]) return NO;
    if (chmod(tokenFile.fileSystemRepresentation, 0600) != 0) {
        if (error) *error = CMCError(@"The protected application session could not be secured.");
        return NO;
    }

    NSURL *codexHome = [self codexHomeDirectory:error];
    if (codexHome == nil) return NO;
    NSTask *task = [[NSTask alloc] init];
    task.executableURL = serverExecutable;
    task.arguments = @[
        @"--host", CMCLoopbackHost,
        @"--port", @"0",
        @"--codex-home", codexHome.path,
        @"--app-home", appSupport.path,
        @"--static-dir", staticDirectory.path,
        @"--token-file", tokenFile.path,
        @"--ready-file", readyFile.path,
    ];
    task.standardOutput = NSFileHandle.fileHandleWithNullDevice;
    task.standardError = NSFileHandle.fileHandleWithNullDevice;
    __weak typeof(self) weakSelf = self;
    task.terminationHandler = ^(NSTask *finishedTask) {
        dispatch_async(dispatch_get_main_queue(), ^{
            typeof(self) strongSelf = weakSelf;
            if (strongSelf == nil || strongSelf.isTerminating || strongSelf.webView == nil) return;
            [strongSelf presentFatalError:@"The application service stopped unexpectedly."];
        });
    };
    if (![task launchAndReturnError:error]) return NO;
    self.serverTask = task;
    [self waitForServer:task readyFile:readyFile token:token];
    return YES;
}

- (NSURL *)applicationSupportDirectory:(NSError **)error {
    NSString *override = NSProcessInfo.processInfo.environment[@"CLEAN_MY_CODEX_APP_HOME"];
    NSURL *directory = nil;
    if (override.length > 0) {
        NSString *path = override.stringByExpandingTildeInPath;
        if (!path.isAbsolutePath) {
            if (error) *error = CMCError(@"The application data override must be an absolute path.");
            return nil;
        }
        directory = [NSURL fileURLWithPath:path isDirectory:YES].URLByStandardizingPath;
    } else {
        NSURL *base = [NSFileManager.defaultManager URLsForDirectory:NSApplicationSupportDirectory
                                                           inDomains:NSUserDomainMask].firstObject;
        if (base == nil) {
            if (error) *error = CMCError(@"Application Support is unavailable.");
            return nil;
        }
        directory = [base URLByAppendingPathComponent:CMCAppName isDirectory:YES];
    }
    if (![NSFileManager.defaultManager createDirectoryAtURL:directory
                                withIntermediateDirectories:YES
                                                 attributes:@{NSFilePosixPermissions: @0700}
                                                      error:error]) {
        return nil;
    }
    chmod(directory.fileSystemRepresentation, 0700);
    return directory;
}

- (NSURL *)codexHomeDirectory:(NSError **)error {
    NSString *override = NSProcessInfo.processInfo.environment[@"CLEAN_MY_CODEX_HOME"];
    if (override.length == 0) {
        return [NSFileManager.defaultManager.homeDirectoryForCurrentUser
            URLByAppendingPathComponent:@".codex"
                             isDirectory:YES];
    }
    NSString *path = override.stringByExpandingTildeInPath;
    if (!path.isAbsolutePath) {
        if (error) *error = CMCError(@"The Codex data override must be an absolute path.");
        return nil;
    }
    return [NSURL fileURLWithPath:path isDirectory:YES].URLByStandardizingPath;
}

- (NSString *)sessionToken:(NSError **)error {
    uint8_t bytes[32];
    if (SecRandomCopyBytes(kSecRandomDefault, sizeof(bytes), bytes) != errSecSuccess) {
        if (error) *error = CMCError(@"A protected application session could not be created.");
        return nil;
    }
    NSData *data = [NSData dataWithBytes:bytes length:sizeof(bytes)];
    NSString *token = [data base64EncodedStringWithOptions:0];
    token = [token stringByReplacingOccurrencesOfString:@"+" withString:@"-"];
    token = [token stringByReplacingOccurrencesOfString:@"/" withString:@"_"];
    return [token stringByReplacingOccurrencesOfString:@"=" withString:@""];
}

- (void)waitForServer:(NSTask *)task readyFile:(NSURL *)readyFile token:(NSString *)token {
    __weak typeof(self) weakSelf = self;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:15];
        while ([deadline timeIntervalSinceNow] > 0) {
            if ([NSFileManager.defaultManager fileExistsAtPath:readyFile.path]) {
                NSError *readyError = nil;
                NSInteger port = [weakSelf readyPortFromFile:readyFile error:&readyError];
                if (port > 0) {
                    dispatch_async(dispatch_get_main_queue(), ^{
                        [weakSelf showInterfaceOnPort:port token:token];
                    });
                    return;
                }
                dispatch_async(dispatch_get_main_queue(), ^{
                    [weakSelf presentFatalError:readyError.localizedDescription];
                });
                return;
            }
            if (!task.running) {
                dispatch_async(dispatch_get_main_queue(), ^{
                    [weakSelf presentFatalError:@"The application service stopped before the window was ready."];
                });
                return;
            }
            [NSThread sleepForTimeInterval:0.1];
        }
        dispatch_async(dispatch_get_main_queue(), ^{
            [weakSelf presentFatalError:@"The application service did not become ready in time."];
        });
    });
}

- (NSInteger)readyPortFromFile:(NSURL *)readyFile error:(NSError **)error {
    NSNumber *regular = nil;
    NSNumber *symbolicLink = nil;
    if (![readyFile getResourceValue:&regular forKey:NSURLIsRegularFileKey error:error] ||
        ![readyFile getResourceValue:&symbolicLink forKey:NSURLIsSymbolicLinkKey error:error] ||
        !regular.boolValue || symbolicLink.boolValue) {
        if (error && *error == nil) *error = CMCError(@"The protected application session could not be verified.");
        return 0;
    }
    NSDictionary *attributes = [NSFileManager.defaultManager attributesOfItemAtPath:readyFile.path error:error];
    if (attributes == nil) return 0;
    NSUInteger permissions = [attributes[NSFilePosixPermissions] unsignedIntegerValue];
    uid_t owner = [attributes[NSFileOwnerAccountID] unsignedIntValue];
    if ((permissions & 0077) != 0 || owner != getuid()) {
        if (error) *error = CMCError(@"The protected application session has unsafe permissions.");
        return 0;
    }
    NSData *data = [NSData dataWithContentsOfURL:readyFile options:0 error:error];
    if (data == nil) return 0;
    NSDictionary *payload = [NSJSONSerialization JSONObjectWithData:data options:0 error:error];
    NSNumber *port = [payload isKindOfClass:NSDictionary.class] ? payload[@"port"] : nil;
    NSInteger value = [port integerValue];
    if (![port isKindOfClass:NSNumber.class] || value < 1 || value > 65535) {
        if (error) *error = CMCError(@"The protected application address is invalid.");
        return 0;
    }
    return value;
}

- (void)showInterfaceOnPort:(NSInteger)port token:(NSString *)token {
    if (self.window == nil) return;
    self.allowedPort = port;

    WKWebViewConfiguration *configuration = [[WKWebViewConfiguration alloc] init];
    configuration.websiteDataStore = WKWebsiteDataStore.nonPersistentDataStore;
    WKWebView *webView = [[WKWebView alloc] initWithFrame:self.window.contentView.bounds
                                            configuration:configuration];
    webView.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    webView.navigationDelegate = self;
    webView.UIDelegate = self;
    self.window.contentView = webView;
    self.webView = webView;

    NSString *address = [NSString stringWithFormat:@"http://%@:%ld/#token=%@",
                                                   CMCLoopbackHost,
                                                   (long)port,
                                                   token];
    NSURL *url = [NSURL URLWithString:address];
    if (url == nil) {
        [self presentFatalError:@"The protected application address could not be created."];
        return;
    }
    NSURLRequest *request = [NSURLRequest requestWithURL:url
                                            cachePolicy:NSURLRequestReloadIgnoringLocalCacheData
                                        timeoutInterval:15];
    [webView loadRequest:request];
}

- (BOOL)isAllowedLocalURL:(NSURL *)url {
    return [url.scheme.lowercaseString isEqualToString:@"http"] &&
           [url.host isEqualToString:CMCLoopbackHost] &&
           url.port.integerValue == self.allowedPort;
}

- (BOOL)isAllowedExternalURL:(NSURL *)url {
    if (![url.scheme.lowercaseString isEqualToString:@"https"] ||
        ![url.host.lowercaseString isEqualToString:@"github.com"]) {
        return NO;
    }
    NSString *path = url.path.lowercaseString;
    return [path isEqualToString:@"/ethan3113"] ||
           [path isEqualToString:@"/ethan3113/"] ||
           [path isEqualToString:@"/ethan3113/clean-my-codex"] ||
           [path hasPrefix:@"/ethan3113/clean-my-codex/"];
}

- (void)webView:(WKWebView *)webView
    decidePolicyForNavigationAction:(WKNavigationAction *)navigationAction
                    decisionHandler:(void (^)(WKNavigationActionPolicy))decisionHandler {
    NSURL *url = navigationAction.request.URL;
    if (url != nil && [self isAllowedLocalURL:url]) {
        decisionHandler(WKNavigationActionPolicyAllow);
        return;
    }
    if (url != nil && [self isAllowedExternalURL:url]) {
        [NSWorkspace.sharedWorkspace openURL:url];
    }
    decisionHandler(WKNavigationActionPolicyCancel);
}

- (WKWebView *)webView:(WKWebView *)webView
    createWebViewWithConfiguration:(WKWebViewConfiguration *)configuration
               forNavigationAction:(WKNavigationAction *)navigationAction
                    windowFeatures:(WKWindowFeatures *)windowFeatures {
    NSURL *url = navigationAction.request.URL;
    if (url != nil && [self isAllowedLocalURL:url]) {
        [webView loadRequest:[NSURLRequest requestWithURL:url]];
    } else if (url != nil && [self isAllowedExternalURL:url]) {
        [NSWorkspace.sharedWorkspace openURL:url];
    }
    return nil;
}

- (void)webViewWebContentProcessDidTerminate:(WKWebView *)webView {
    [webView reload];
}

- (void)requestGracefulShutdown {
    NSString *address = [NSString stringWithFormat:@"http://%@:%ld%@",
                                                   CMCLoopbackHost,
                                                   (long)self.allowedPort,
                                                   CMCShutdownPath];
    NSURL *url = [NSURL URLWithString:address];
    if (url == nil) {
        self.terminationPending = NO;
        self.isTerminating = NO;
        [NSApp replyToApplicationShouldTerminate:NO];
        [self presentShutdownError:@"The protected shutdown address could not be created."];
        return;
    }

    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url
                                                           cachePolicy:NSURLRequestReloadIgnoringLocalCacheData
                                                       timeoutInterval:24 * 60 * 60];
    request.HTTPMethod = @"POST";
    request.HTTPBody = [@"{}" dataUsingEncoding:NSUTF8StringEncoding];
    [request setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
    [request setValue:self.sessionToken forHTTPHeaderField:CMCSessionHeader];

    NSURLSessionConfiguration *configuration = NSURLSessionConfiguration.ephemeralSessionConfiguration;
    configuration.timeoutIntervalForRequest = 24 * 60 * 60;
    configuration.timeoutIntervalForResource = 24 * 60 * 60;
    NSURLSession *session = [NSURLSession sessionWithConfiguration:configuration];
    __weak typeof(self) weakSelf = self;
    NSURLSessionDataTask *task = [session
        dataTaskWithRequest:request
          completionHandler:^(NSData *data, NSURLResponse *response, NSError *requestError) {
              [session finishTasksAndInvalidate];
              typeof(self) strongSelf = weakSelf;
              if (strongSelf == nil) return;

              BOOL safeToTerminate = !strongSelf.serverTask.running;
              NSString *failure = requestError.localizedDescription;
              if (!safeToTerminate && requestError == nil &&
                  [response isKindOfClass:NSHTTPURLResponse.class] &&
                  ((NSHTTPURLResponse *)response).statusCode == 200) {
                  NSError *jsonError = nil;
                  NSDictionary *payload = data.length > 0
                      ? [NSJSONSerialization JSONObjectWithData:data options:0 error:&jsonError]
                      : nil;
                  safeToTerminate = [payload isKindOfClass:NSDictionary.class] &&
                                    [payload[@"safe_to_terminate"] boolValue];
                  if (!safeToTerminate) {
                      failure = jsonError.localizedDescription ?: @"The application service did not confirm a safe shutdown.";
                  }
              }

              dispatch_async(dispatch_get_main_queue(), ^{
                  if (safeToTerminate) {
                      strongSelf.terminationApproved = YES;
                      [NSApp replyToApplicationShouldTerminate:YES];
                      return;
                  }
                  strongSelf.terminationPending = NO;
                  strongSelf.isTerminating = NO;
                  [NSApp replyToApplicationShouldTerminate:NO];
                  [strongSelf presentShutdownError:failure];
              });
          }];
    [task resume];
}

- (void)presentShutdownError:(NSString *)message {
    NSAlert *alert = [[NSAlert alloc] init];
    alert.alertStyle = NSAlertStyleWarning;
    alert.messageText = @"Clean My Codex is still working";
    alert.informativeText = [NSString stringWithFormat:
        @"Quit was paused to protect the current operation. %@",
        message.length > 0 ? message : @"Wait for the operation to finish, then try again."];
    [alert addButtonWithTitle:@"Keep App Open"];
    if (self.window != nil) {
        [alert beginSheetModalForWindow:self.window completionHandler:nil];
    } else {
        [alert runModal];
    }
}

- (void)presentFatalError:(NSString *)message {
    if (self.didPresentFatalError) return;
    self.didPresentFatalError = YES;
    [self stopServer];

    NSAlert *alert = [[NSAlert alloc] init];
    alert.alertStyle = NSAlertStyleCritical;
    alert.messageText = [NSString stringWithFormat:@"%@ could not start", CMCAppName];
    alert.informativeText = message.length > 0 ? message : @"An unknown startup error occurred.";
    [alert addButtonWithTitle:@"Quit"];
    if (self.window != nil) {
        [alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse returnCode) {
            [NSApp terminate:nil];
        }];
    } else {
        [alert runModal];
        [NSApp terminate:nil];
    }
}

- (void)stopServer {
    NSTask *task = self.serverTask;
    if (task == nil || !task.running) return;
    [task terminate];
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:2];
    while (task.running && [deadline timeIntervalSinceNow] > 0) {
        [NSThread sleepForTimeInterval:0.05];
    }
    if (task.running) kill(task.processIdentifier, SIGKILL);
}

- (void)removeSessionDirectory {
    if (self.sessionDirectory == nil) return;
    [NSFileManager.defaultManager removeItemAtURL:self.sessionDirectory error:nil];
    self.sessionDirectory = nil;
}

@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *application = NSApplication.sharedApplication;
        CMCAppDelegate *delegate = [[CMCAppDelegate alloc] init];
        application.delegate = delegate;
        [application setActivationPolicy:NSApplicationActivationPolicyRegular];
        [delegate installMainMenu];
        [application run];
    }
    return 0;
}
