# main.py - Standalone Game Soundtrack Downloader
import os
import threading
import time
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.scrollview import ScrollView
from kivy.uix.gridlayout import GridLayout
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.clock import Clock
from kivy.logger import Logger

# Android-specific imports
try:
    from android.permissions import request_permissions, Permission
    from android.storage import primary_external_storage_path
    ANDROID = True
except ImportError:
    ANDROID = False

# Import our downloader (simplified version)
try:
    import requests
    from bs4 import BeautifulSoup
    import urllib.parse
    DOWNLOADS_AVAILABLE = True
except ImportError:
    DOWNLOADS_AVAILABLE = False

class SimpleKHDownloader:
    """Simplified KHInsider downloader for mobile"""
    def __init__(self):
        self.base_url = "https://downloads.khinsider.com"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36'
        })

    def search(self, query):
        """Search for soundtracks"""
        try:
            search_url = f"{self.base_url}/search"
            params = {'search': query}
            resp = self.session.get(search_url, params=params, timeout=10)
            soup = BeautifulSoup(resp.content, 'html.parser')
            
            results = []
            for link in soup.find_all('a', href=lambda x: x and '/game-soundtracks/album/' in x):
                name = link.get_text(strip=True)
                url = link.get('href')
                if url.startswith('/'):
                    url = self.base_url + url
                album_id = url.split('/album/')[-1]
                if name and len(name) > 2:
                    results.append({'name': name, 'id': album_id, 'url': url})
                if len(results) >= 10:  # Limit for mobile
                    break
            return results
        except Exception as e:
            Logger.error(f"Search error: {e}")
            return []

    def get_album_info(self, album_url):
        """Get album information and track list"""
        try:
            resp = self.session.get(album_url, timeout=10)
            soup = BeautifulSoup(resp.content, 'html.parser')
            
            # Get title
            title = soup.title.get_text().strip() if soup.title else "Unknown Album"
            title = title.replace(' - Download', '').replace(' - KHInsider', '')
            
            # Get tracks
            tracks = []
            table = soup.find('table', id='songlist')
            if table:
                for i, row in enumerate(table.find_all('tr'), 1):
                    cell = row.find('td', class_='clickable-row')
                    if cell:
                        link = cell.find('a', href=True)
                        if link:
                            track_name = link.get_text(strip=True)
                            track_url = link.get('href')
                            if track_url.startswith('/'):
                                track_url = self.base_url + track_url
                            tracks.append({
                                'number': i,
                                'name': track_name,
                                'url': track_url
                            })
            
            return {'title': title, 'tracks': tracks}
        except Exception as e:
            Logger.error(f"Album info error: {e}")
            return None

class ResultItem(BoxLayout):
    """Widget for displaying search results"""
    def __init__(self, result, app_instance, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'horizontal'
        self.size_hint_y = None
        self.height = '60dp'
        self.spacing = '10dp'
        self.padding = '10dp'
        
        # Album name
        name_label = Label(
            text=result['name'],
            text_size=(None, None),
            halign='left',
            size_hint_x=0.8
        )
        
        # View button
        view_btn = Button(
            text='View',
            size_hint_x=0.2,
            on_press=lambda x: app_instance.view_album(result)
        )
        
        self.add_widget(name_label)
        self.add_widget(view_btn)

class TrackItem(BoxLayout):
    """Widget for displaying tracks"""
    def __init__(self, track, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'horizontal'
        self.size_hint_y = None
        self.height = '50dp'
        self.spacing = '10dp'
        self.padding = '5dp'
        
        # Track number
        num_label = Label(
            text=str(track['number']),
            size_hint_x=0.1,
            text_size=(None, None)
        )
        
        # Track name
        name_label = Label(
            text=track['name'],
            text_size=(None, None),
            halign='left',
            size_hint_x=0.9
        )
        
        self.add_widget(num_label)
        self.add_widget(name_label)

class GameSoundtrackApp(App):
    def build(self):
        Logger.info("Building Game Soundtrack App")
        
        # Request permissions
        if ANDROID:
            try:
                permissions = [
                    Permission.INTERNET,
                    Permission.ACCESS_NETWORK_STATE,
                    Permission.READ_EXTERNAL_STORAGE,
                    Permission.WRITE_EXTERNAL_STORAGE,
                ]
                request_permissions(permissions)
            except Exception as e:
                Logger.error(f"Permission error: {e}")
        
        # Initialize downloader
        if DOWNLOADS_AVAILABLE:
            self.downloader = SimpleKHDownloader()
        else:
            self.downloader = None
        
        # Main layout
        main_layout = BoxLayout(orientation='vertical', padding='10dp', spacing='10dp')
        
        # Title
        title = Label(
            text='🎵 Game Soundtrack Downloader 🎵',
            size_hint_y=None,
            height='50dp',
            font_size='18sp'
        )
        main_layout.add_widget(title)
        
        # Search section
        search_layout = BoxLayout(orientation='horizontal', size_hint_y=None, height='50dp', spacing='10dp')
        
        self.search_input = TextInput(
            hint_text='Search for game soundtracks...',
            multiline=False,
            size_hint_x=0.7
        )
        self.search_input.bind(on_text_validate=self.search_soundtracks)
        
        search_btn = Button(
            text='Search',
            size_hint_x=0.3,
            on_press=self.search_soundtracks
        )
        
        search_layout.add_widget(self.search_input)
        search_layout.add_widget(search_btn)
        main_layout.add_widget(search_layout)
        
        # Status label
        self.status_label = Label(
            text='Ready to search!',
            size_hint_y=None,
            height='30dp',
            color=(0.7, 0.7, 0.7, 1)
        )
        main_layout.add_widget(self.status_label)
        
        # Results area
        self.results_layout = GridLayout(cols=1, size_hint_y=None, spacing='5dp')
        self.results_layout.bind(minimum_height=self.results_layout.setter('height'))
        
        results_scroll = ScrollView()
        results_scroll.add_widget(self.results_layout)
        main_layout.add_widget(results_scroll)
        
        # Check if downloads are available
        if not DOWNLOADS_AVAILABLE:
            self.status_label.text = 'Download functionality not available (missing dependencies)'
        
        return main_layout
    
    def search_soundtracks(self, instance=None):
        """Search for soundtracks"""
        query = self.search_input.text.strip()
        if not query:
            self.status_label.text = 'Please enter a search term'
            return
        
        if not self.downloader:
            self.status_label.text = 'Download functionality not available'
            return
        
        self.status_label.text = 'Searching...'
        self.results_layout.clear_widgets()
        
        # Run search in background thread
        threading.Thread(target=self._search_thread, args=(query,), daemon=True).start()
    
    def _search_thread(self, query):
        """Background search thread"""
        try:
            results = self.downloader.search(query)
            Clock.schedule_once(lambda dt: self._display_results(results))
        except Exception as e:
            Clock.schedule_once(lambda dt: self._show_error(f"Search failed: {e}"))
    
    def _display_results(self, results):
        """Display search results"""
        self.results_layout.clear_widgets()
        
        if not results:
            self.status_label.text = 'No results found'
            return
        
        self.status_label.text = f'Found {len(results)} albums'
        
        for result in results:
            item = ResultItem(result, self)
            self.results_layout.add_widget(item)
    
    def view_album(self, result):
        """View album details"""
        self.status_label.text = f'Loading {result["name"]}...'
        threading.Thread(target=self._load_album_thread, args=(result,), daemon=True).start()
    
    def _load_album_thread(self, result):
        """Background album loading thread"""
        try:
            album_info = self.downloader.get_album_info(result['url'])
            Clock.schedule_once(lambda dt: self._show_album(album_info))
        except Exception as e:
            Clock.schedule_once(lambda dt: self._show_error(f"Failed to load album: {e}"))
    
    def _show_album(self, album_info):
        """Show album in popup"""
        if not album_info:
            self.status_label.text = 'Failed to load album'
            return
        
        # Create popup content
        content = BoxLayout(orientation='vertical', spacing='10dp')
        
        # Album title
        title_label = Label(
            text=album_info['title'],
            size_hint_y=None,
            height='40dp',
            font_size='16sp'
        )
        content.add_widget(title_label)
        
        # Track count
        count_label = Label(
            text=f'{len(album_info["tracks"])} tracks',
            size_hint_y=None,
            height='30dp',
            color=(0.7, 0.7, 0.7, 1)
        )
        content.add_widget(count_label)
        
        # Track list
        tracks_layout = GridLayout(cols=1, size_hint_y=None, spacing='2dp')
        tracks_layout.bind(minimum_height=tracks_layout.setter('height'))
        
        for track in album_info['tracks'][:20]:  # Limit to first 20 tracks
            track_item = TrackItem(track)
            tracks_layout.add_widget(track_item)
        
        tracks_scroll = ScrollView()
        tracks_scroll.add_widget(tracks_layout)
        content.add_widget(tracks_scroll)
        
        # Close button
        close_btn = Button(
            text='Close',
            size_hint_y=None,
            height='40dp'
        )
        content.add_widget(close_btn)
        
        # Create and show popup
        popup = Popup(
            title='Album Details',
            content=content,
            size_hint=(0.9, 0.8)
        )
        close_btn.bind(on_press=popup.dismiss)
        popup.open()
        
        self.status_label.text = 'Album loaded successfully'
    
    def _show_error(self, message):
        """Show error message"""
        self.status_label.text = message
    
    def on_pause(self):
        """Handle app pause"""
        return True
    
    def on_resume(self):
        """Handle app resume"""
        pass

if __name__ == '__main__':
    GameSoundtrackApp().run()
