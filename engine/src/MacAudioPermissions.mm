#import "MacAudioPermissions.h"
#import <AVFoundation/AVFoundation.h>

void requestMacOSMicrophonePermission() {
    if (@available(macOS 10.14, *)) {
        AVAuthorizationStatus status = [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio];
        
        if (status == AVAuthorizationStatusNotDetermined) {
            [AVCaptureDevice requestAccessForMediaType:AVMediaTypeAudio 
                completionHandler:^(BOOL granted) {
                    // Permission response received
                    (void)granted;
                }];
        }
    }
}

