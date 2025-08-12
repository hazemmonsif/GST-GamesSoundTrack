# main.py - Fixed Android version without androidx_webview
import threading, socket, time, os
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.clock import Clock
from kivy.logger import Logger

# Android-specific imports
try:
    from android.permissions import request_permissions, Permission
    from android import mActivity
    ANDROID = True
except ImportError:
    ANDROID = False
    Logger.info("Not running on Android")

def find_free_port():
    """Find a free port for the Flask server"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return port
    except Exception as e:
        Logger.error(f"Error finding free port: {e}")
        return 5000  # fallback port

def run_flask_on(port):
    """Run Flask server in a separate thread"""
    try:
        from app import app
        Logger.info(f"Starting Flask server on port {port}")
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
    except Exception as e:
        Logger.error(f"Flask server error: {e}")

class Root(BoxLayout):
    pass

class GSDApp(App):
    def build(self):
        Logger.info("Building GSD App")
        
        # Request Android permissions if on Android
        if ANDROID:
            self.request_android_permissions()
        
        # Find port and start Flask server
        self.port = find_free_port()
        Logger.info(f"Using port: {self.port}")
        
        # Start Flask in background thread
        flask_thread = threading.Thread(target=run_flask_on, args=(self.port,), daemon=True)
        flask_thread.start()
        Logger.info("Flask thread started")
        
        # Create simple UI without webview for now
        root = Root(orientation="vertical")
        
        # Use a simple label since webview is causing issues
        info_label = Label(
            text=f"🎵 Game Soundtrack Downloader 🎵\n\n"
                 f"Server running on:\nhttp://127.0.0.1:{self.port}/\n\n"
                 f"Open this URL in your phone's browser\nto use the app!\n\n"
                 f"Features:\n• Search soundtracks\n• Preview tracks\n• Download albums",
            text_size=(None, None),
            halign="center",
            font_size='16sp'
        )
        root.add_widget(info_label)
        
        Logger.info("App build complete")
        return root

    def request_android_permissions(self):
        """Request necessary Android permissions"""
        try:
            permissions = [
                Permission.INTERNET,
                Permission.ACCESS_NETWORK_STATE,
                Permission.READ_EXTERNAL_STORAGE,
                Permission.WRITE_EXTERNAL_STORAGE,
            ]
            request_permissions(permissions)
            Logger.info("Android permissions requested")
        except Exception as e:
            Logger.error(f"Permission request error: {e}")

    def on_pause(self):
        """Handle app pause (Android lifecycle)"""
        Logger.info("App paused")
        return True

    def on_resume(self):
        """Handle app resume (Android lifecycle)"""
        Logger.info("App resumed")

if __name__ == "__main__":
    try:
        Logger.info("Starting GSD App")
        GSDApp().run()
    except Exception as e:
        Logger.error(f"App crash: {e}")
        import traceback
        Logger.error(traceback.format_exc())
