"""
Game Soundtrack Downloader - Native Android App
A proper mobile app for downloading game soundtracks from KHInsider
"""

import os
import json
import threading
import time
import random
import re
import urllib.parse
from pathlib import Path

# Kivy imports
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.scrollview import ScrollView
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.uix.checkbox import CheckBox
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.actionbar import ActionBar, ActionView, ActionPrevious, ActionButton
from kivy.uix.image import AsyncImage
from kivy.clock import Clock
from kivy.logger import Logger
from kivy.metrics import dp
from kivy.core.window import Window

# Network imports
import requests
from bs4 import BeautifulSoup

# Android-specific imports
try:
    from android.permissions import request_permissions, Permission
    from android.storage import primary_external_storage_path
    from android import mActivity
    ANDROID = True
    Logger.info("Running on Android")
except ImportError:
    ANDROID = False
    Logger.info("Not running on Android")

# Set window size for development
if not ANDROID:
    Window.size = (360, 640)

class KHInsiderDownloader:
    """Enhanced KHInsider downloader for mobile"""
    
    def __init__(self):
        self.base_url = "https://downloads.khinsider.com"
        self.session = requests.Session()
        self.user_agents = [
            'Mozilla/5.0 (Linux; Android 11; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.120 Mobile Safari/537.36',
            'Mozilla/5.0 (Linux; Android 10; Pixel 4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.210 Mobile Safari/537.36'
        ]
        self._setup_session()

    def _setup_session(self):
        self.session.headers.update({
            'User-Agent': random.choice(self.user_agents),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })

    def search(self, query, max_results=20):
        """Search for game soundtracks"""
        try:
            search_url = f"{self.base_url}/search"
            params = {'search': query}
            
            Logger.info(f"Searching for: {query}")
            resp = self.session.get(search_url, params=params, timeout=15)
            resp.raise_for_status()
            
            soup = BeautifulSoup(resp.content, 'html.parser')
            results = []

            # Look for album table
            table = soup.find('table', {'id': 'albumlist'}) or soup.find('table', {'class': 'albumlist'})
            if table:
                for row in table.find_all('tr'):
                    if row.find('th'):  # Skip header row
                        continue
                    
                    link = row.find('a', href=lambda x: x and '/game-soundtracks/album/' in x)
                    if not link:
                        continue
                    
                    album_url = link.get('href')
                    if album_url.startswith('/'):
                        album_url = self.base_url + album_url
                    
                    album_id = album_url.split('/album/')[-1]
                    name = link.get_text(strip=True)
                    
                    # Try to find album cover
                    icon = None
                    img = row.find('img')
                    if img and img.get('src'):
                        src = img.get('src')
                        if src.startswith('/'):
                            icon = self.base_url + src
                        elif src.startswith('http'):
                            icon = src
                    
                    if name and len(name) > 2:
                        results.append({
                            'id': album_id,
                            'name': name,
                            'url': album_url,
                            'icon': icon
                        })
                    
                    if len(results) >= max_results:
                        break

            Logger.info(f"Found {len(results)} results")
            return results[:max_results]
            
        except Exception as e:
            Logger.error(f"Search error: {e}")
            return []

    def get_album_info(self, album_url):
        """Get detailed album information"""
        try:
            Logger.info(f"Loading album: {album_url}")
            resp = self.session.get(album_url, timeout=15)
            resp.raise_for_status()
            
            soup = BeautifulSoup(resp.content, 'html.parser')
            
            # Extract title
            title = "Unknown Album"
            if soup.title:
                title = soup.title.get_text().strip()
                title = re.sub(r'\s*-\s*(Download|KHInsider|MP3).*$', '', title, flags=re.I)
            
            # Extract album cover
            icon = None
            for img in soup.find_all('img'):
                src = img.get('src', '')
                alt = img.get('alt', '').lower()
                if any(k in src.lower() for k in ['album', 'cover', 'artwork']) or \
                   any(k in alt for k in ['album', 'cover', 'artwork']):
                    if src.startswith('/'):
                        icon = self.base_url + src
                    elif src.startswith('http'):
                        icon = src
                    break
            
            # Extract tracks
            tracks = []
            table = soup.find('table', id='songlist')
            if table:
                track_num = 1
                for row in table.find_all('tr'):
                    cell = row.find('td', class_='clickable-row')
                    if not cell:
                        continue
                    
                    link = cell.find('a', href=True)
                    if not link:
                        continue
                    
                    track_name = link.get_text(strip=True)
                    track_url = link.get('href')
                    
                    if track_url.startswith('/'):
                        track_url = self.base_url + track_url
                    
                    if track_name and track_url:
                        tracks.append({
                            'number': track_num,
                            'name': track_name,
                            'url': track_url
                        })
                        track_num += 1
            
            Logger.info(f"Album loaded: {title} with {len(tracks)} tracks")
            return {
                'title': title,
                'icon': icon,
                'tracks': tracks,
                'total_tracks': len(tracks)
            }
            
        except Exception as e:
            Logger.error(f"Album loading error: {e}")
            return None

# UI Components
class SearchScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.name = 'search'
        
        # Main layout
        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        
        # Action bar
        action_bar = ActionBar()
        action_view = ActionView()
        action_view.add_widget(ActionPrevious(title='Game Soundtrack Downloader', with_previous=False))
        action_bar.add_widget(action_view)
        layout.add_widget(action_bar)
        
        # Search section
        search_box = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(120), spacing=dp(10))
        
        # Title
        title = Label(
            text='🎵 Search Game Soundtracks 🎵',
            font_size='18sp',
            size_hint_y=None,
            height=dp(40),
            color=(0.2, 0.6, 1, 1)
        )
        search_box.add_widget(title)
        
        # Search input
        search_layout = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(50), spacing=dp(10))
        
        self.search_input = TextInput(
            hint_text='Enter game name (e.g. "mario", "zelda", "final fantasy")',
            multiline=False,
            size_hint_x=0.75,
            font_size='14sp'
        )
        self.search_input.bind(on_text_validate=self.search_action)
        
        search_btn = Button(
            text='Search',
            size_hint_x=0.25,
            font_size='14sp'
        )
        search_btn.bind(on_press=self.search_action)
        
        search_layout.add_widget(self.search_input)
        search_layout.add_widget(search_btn)
        search_box.add_widget(search_layout)
        
        layout.add_widget(search_box)
        
        # Status label
        self.status_label = Label(
            text='Enter a game name to search for soundtracks',
            size_hint_y=None,
            height=dp(30),
            color=(0.7, 0.7, 0.7, 1),
            font_size='12sp'
        )
        layout.add_widget(self.status_label)
        
        # Results area
        self.results_layout = GridLayout(cols=1, size_hint_y=None, spacing=dp(5))
        self.results_layout.bind(minimum_height=self.results_layout.setter('height'))
        
        self.scroll_view = ScrollView()
        self.scroll_view.add_widget(self.results_layout)
        layout.add_widget(self.scroll_view)
        
        self.add_widget(layout)
    
    def search_action(self, instance):
        query = self.search_input.text.strip()
        if not query:
            self.status_label.text = 'Please enter a game name'
            return
        
        app = App.get_running_app()
        if not hasattr(app, 'downloader'):
            self.status_label.text = 'Downloader not available'
            return
        
        self.status_label.text = 'Searching...'
        self.results_layout.clear_widgets()
        
        # Search in background thread
        threading.Thread(target=self._search_thread, args=(query,), daemon=True).start()
    
    def _search_thread(self, query):
        try:
            app = App.get_running_app()
            results = app.downloader.search(query)
            Clock.schedule_once(lambda dt: self._display_results(results))
        except Exception as e:
            Clock.schedule_once(lambda dt: self._show_error(f"Search failed: {e}"))
    
    def _display_results(self, results):
        self.results_layout.clear_widgets()
        
        if not results:
            self.status_label.text = 'No soundtracks found. Try a different search term.'
            return
        
        self.status_label.text = f'Found {len(results)} soundtracks'
        
        for result in results:
            item = ResultCard(result)
            self.results_layout.add_widget(item)
    
    def _show_error(self, message):
        self.status_label.text = message

class ResultCard(BoxLayout):
    def __init__(self, result, **kwargs):
        super().__init__(**kwargs)
        self.result = result
        self.orientation = 'horizontal'
        self.size_hint_y = None
        self.height = dp(80)
        self.spacing = dp(10)
        self.padding = dp(10)
        
        # Album cover (placeholder or actual)
        if result.get('icon'):
            cover = AsyncImage(
                source=result['icon'],
                size_hint_x=None,
                width=dp(60)
            )
        else:
            cover = Label(
                text='🎵',
                font_size='24sp',
                size_hint_x=None,
                width=dp(60)
            )
        
        # Album info
        info_layout = BoxLayout(orientation='vertical', spacing=dp(2))
        
        name_label = Label(
            text=result['name'],
            text_size=(None, None),
            halign='left',
            font_size='14sp',
            color=(1, 1, 1, 1)
        )
        
        id_label = Label(
            text=f"ID: {result['id']}",
            text_size=(None, None),
            halign='left',
            font_size='10sp',
            color=(0.7, 0.7, 0.7, 1)
        )
        
        info_layout.add_widget(name_label)
        info_layout.add_widget(id_label)
        
        # View button
        view_btn = Button(
            text='View\nAlbum',
            size_hint_x=None,
            width=dp(70),
            font_size='12sp'
        )
        view_btn.bind(on_press=self.view_album)
        
        self.add_widget(cover)
        self.add_widget(info_layout)
        self.add_widget(view_btn)
    
    def view_album(self, instance):
        app = App.get_running_app()
        app.load_album(self.result)

class AlbumScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.name = 'album'
        self.album_data = None
        
        # Main layout
        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        
        # Action bar with back button
        action_bar = ActionBar()
        action_view = ActionView()
        back_btn = ActionPrevious(title='Album Details')
        back_btn.bind(on_press=self.go_back)
        action_view.add_widget(back_btn)
        action_bar.add_widget(action_view)
        layout.add_widget(action_bar)
        
        # Album header
        header_layout = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(100), spacing=dp(10))
        
        # Album cover
        self.cover_image = AsyncImage(
            size_hint_x=None,
            width=dp(80)
        )
        
        # Album info
        info_layout = BoxLayout(orientation='vertical', spacing=dp(5))
        
        self.album_title = Label(
            text='Loading...',
            font_size='16sp',
            text_size=(None, None),
            halign='left',
            color=(0.2, 0.6, 1, 1)
        )
        
        self.track_count = Label(
            text='',
            font_size='12sp',
            text_size=(None, None),
            halign='left',
            color=(0.7, 0.7, 0.7, 1)
        )
        
        info_layout.add_widget(self.album_title)
        info_layout.add_widget(self.track_count)
        
        header_layout.add_widget(self.cover_image)
        header_layout.add_widget(info_layout)
        layout.add_widget(header_layout)
        
        # Status
        self.status_label = Label(
            text='Loading album...',
            size_hint_y=None,
            height=dp(30),
            font_size='12sp'
        )
        layout.add_widget(self.status_label)
        
        # Track list
        self.tracks_layout = GridLayout(cols=1, size_hint_y=None, spacing=dp(2))
        self.tracks_layout.bind(minimum_height=self.tracks_layout.setter('height'))
        
        scroll = ScrollView()
        scroll.add_widget(self.tracks_layout)
        layout.add_widget(scroll)
        
        self.add_widget(layout)
    
    def load_album(self, album_result):
        """Load album data"""
        self.album_title.text = album_result['name']
        self.track_count.text = 'Loading tracks...'
        self.tracks_layout.clear_widgets()
        
        if album_result.get('icon'):
            self.cover_image.source = album_result['icon']
        
        # Load in background
        threading.Thread(target=self._load_album_thread, args=(album_result,), daemon=True).start()
    
    def _load_album_thread(self, album_result):
        try:
            app = App.get_running_app()
            album_data = app.downloader.get_album_info(album_result['url'])
            Clock.schedule_once(lambda dt: self._display_album(album_data))
        except Exception as e:
            Clock.schedule_once(lambda dt: self._show_error(f"Failed to load album: {e}"))
    
    def _display_album(self, album_data):
        if not album_data:
            self.status_label.text = 'Failed to load album data'
            return
        
        self.album_data = album_data
        self.album_title.text = album_data['title']
        self.track_count.text = f"{album_data['total_tracks']} tracks"
        self.status_label.text = 'Album loaded successfully'
        
        # Display tracks
        for track in album_data['tracks']:
            track_item = TrackItem(track)
            self.tracks_layout.add_widget(track_item)
    
    def _show_error(self, message):
        self.status_label.text = message
    
    def go_back(self, instance):
        app = App.get_running_app()
        app.screen_manager.current = 'search'

class TrackItem(BoxLayout):
    def __init__(self, track, **kwargs):
        super().__init__(**kwargs)
        self.track = track
        self.orientation = 'horizontal'
        self.size_hint_y = None
        self.height = dp(50)
        self.spacing = dp(10)
        self.padding = [dp(10), dp(5)]
        
        # Track number
        num_label = Label(
            text=f"{track['number']:02d}",
            size_hint_x=None,
            width=dp(40),
            font_size='12sp',
            color=(0.7, 0.7, 0.7, 1)
        )
        
        # Track name
        name_label = Label(
            text=track['name'],
            text_size=(None, None),
            halign='left',
            font_size='13sp'
        )
        
        # Play button (placeholder)
        play_btn = Button(
            text='♪',
            size_hint_x=None,
            width=dp(40),
            font_size='16sp'
        )
        play_btn.bind(on_press=self.play_track)
        
        self.add_widget(num_label)
        self.add_widget(name_label)
        self.add_widget(play_btn)
    
    def play_track(self, instance):
        # Placeholder for future play functionality
        Logger.info(f"Would play: {self.track['name']}")

class GameSoundtrackApp(App):
    def build(self):
        Logger.info("Building Game Soundtrack App")
        
        # Request Android permissions
        if ANDROID:
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
                Logger.error(f"Permission error: {e}")
        
        # Initialize downloader
        try:
            self.downloader = KHInsiderDownloader()
            Logger.info("Downloader initialized")
        except Exception as e:
            Logger.error(f"Failed to initialize downloader: {e}")
            self.downloader = None
        
        # Create screen manager
        self.screen_manager = ScreenManager()
        
        # Add screens
        self.search_screen = SearchScreen()
        self.album_screen = AlbumScreen()
        
        self.screen_manager.add_widget(self.search_screen)
        self.screen_manager.add_widget(self.album_screen)
        
        # Start with search screen
        self.screen_manager.current = 'search'
        
        return self.screen_manager
    
    def load_album(self, album_result):
        """Switch to album screen and load album"""
        self.album_screen.load_album(album_result)
        self.screen_manager.current = 'album'
    
    def on_pause(self):
        return True
    
    def on_resume(self):
        pass

if __name__ == '__main__':
    GameSoundtrackApp().run()
