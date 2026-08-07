#import <Cocoa/Cocoa.h>

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2) {
            fprintf(stderr, "Usage: AppIconGenerator <output.png>\n");
            return 2;
        }

        const NSInteger size = 1024;
        NSBitmapImageRep *bitmap = [[NSBitmapImageRep alloc]
            initWithBitmapDataPlanes:NULL
                          pixelsWide:size
                          pixelsHigh:size
                       bitsPerSample:8
                     samplesPerPixel:4
                            hasAlpha:YES
                            isPlanar:NO
                      colorSpaceName:NSDeviceRGBColorSpace
                         bytesPerRow:0
                        bitsPerPixel:0];
        if (bitmap == nil) return 1;

        NSGraphicsContext *context = [NSGraphicsContext graphicsContextWithBitmapImageRep:bitmap];
        [NSGraphicsContext saveGraphicsState];
        NSGraphicsContext.currentContext = context;
        context.shouldAntialias = YES;
        [NSColor.clearColor setFill];
        NSRectFill(NSMakeRect(0, 0, size, size));

        CGFloat scale = (CGFloat)size / 32.0;
        CGContextTranslateCTM(context.CGContext, 0, size);
        CGContextScaleCTM(context.CGContext, scale, -scale);

        NSBezierPath *frame = [NSBezierPath bezierPathWithRoundedRect:NSMakeRect(0.75, 0.75, 30.5, 30.5)
                                                              xRadius:6.25
                                                              yRadius:6.25];
        [[NSColor colorWithCalibratedRed:0.039 green:0.039 blue:0.039 alpha:1] setFill];
        [frame fill];
        [[NSColor colorWithCalibratedRed:0.227 green:0.227 blue:0.227 alpha:1] setStroke];
        frame.lineWidth = 1.5;
        [frame stroke];

        [[NSColor colorWithCalibratedWhite:0.929 alpha:1] setFill];
        [[NSBezierPath bezierPathWithRoundedRect:NSMakeRect(7.5, 8, 6.5, 6.5) xRadius:1.25 yRadius:1.25] fill];

        [[NSColor colorWithCalibratedWhite:0.929 alpha:0.42] setFill];
        [[NSBezierPath bezierPathWithRoundedRect:NSMakeRect(17.5, 8, 7, 6.5) xRadius:1.25 yRadius:1.25] fill];
        [[NSBezierPath bezierPathWithRoundedRect:NSMakeRect(7.5, 18, 6.5, 6) xRadius:1.25 yRadius:1.25] fill];

        NSBezierPath *arrow = [[NSBezierPath alloc] init];
        [arrow moveToPoint:NSMakePoint(24, 18.75)];
        [arrow lineToPoint:NSMakePoint(24, 21.25)];
        [arrow curveToPoint:NSMakePoint(21.25, 24)
              controlPoint1:NSMakePoint(24, 22.77)
              controlPoint2:NSMakePoint(22.77, 24)];
        [arrow lineToPoint:NSMakePoint(17.5, 24)];
        [arrow moveToPoint:NSMakePoint(19.75, 21.75)];
        [arrow lineToPoint:NSMakePoint(17.5, 24)];
        [arrow lineToPoint:NSMakePoint(19.75, 26.25)];
        arrow.lineWidth = 1.7;
        arrow.lineCapStyle = NSLineCapStyleRound;
        arrow.lineJoinStyle = NSLineJoinStyleRound;
        [[NSColor colorWithCalibratedWhite:0.929 alpha:1] setStroke];
        [arrow stroke];

        [NSGraphicsContext restoreGraphicsState];
        NSData *data = [bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
        if (data == nil) return 1;
        NSURL *output = [NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[1]]];
        return [data writeToURL:output atomically:YES] ? 0 : 1;
    }
}
