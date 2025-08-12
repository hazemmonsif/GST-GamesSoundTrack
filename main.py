# main.py
import threading, socket
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.clock import Clock
from kivy_garden.androidx_webview import WebView

def find_free_port():
    import socket as s
    sock = s.socket(s.AF_INET, s.SOCK_STREAM); sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]; sock.close(); return port

def run_flask_on(port):
    from app import app
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)

class Root(BoxLayout):
    pass

class GSDApp(App):
    def build(self):
        self.port = find_free_port()
        t = threading.Thread(target=run_flask_on, args=(self.port,), daemon=True)
        t.start()
        root = Root(orientation="vertical")
        self.wv = WebView()
        root.add_widget(self.wv)
        Clock.schedule_once(lambda *_: self._try_load(0), 0.8)
        return root

    def _try_load(self, tries):
        self.wv.url = f"http://127.0.0.1:{self.port}/"
        if tries < 5:
            Clock.schedule_once(lambda *_: self._try_load(tries+1), 0.8)

if __name__ == "__main__":
    GSDApp().run()