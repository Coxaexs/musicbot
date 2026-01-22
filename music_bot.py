import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button
import asyncio
import yt_dlp
import os
import tempfile
from collections import deque
from dataclasses import dataclass
from typing import Optional
import aiohttp
import ctypes.util
import re
import json

import config

def load_opus():
    """Load the opus library for voice support"""
    if discord.opus.is_loaded():
        print("✅ Opus already loaded")
        return True
    
    opus_paths = [
        # macOS paths
        '/opt/homebrew/lib/libopus.dylib', 
        '/usr/local/lib/libopus.dylib',     
        '/opt/homebrew/opt/opus/lib/libopus.dylib',
        '/usr/local/opt/opus/lib/libopus.dylib',
        # Linux path
        '/usr/lib/x86_64-linux-gnu/libopus.so.0',
        '/usr/lib/aarch64-linux-gnu/libopus.so.0',
        '/usr/lib/libopus.so.0',
        '/usr/lib/libopus.so',
        # Fallback to system library finder
        ctypes.util.find_library('opus'),
    ]
    
    for path in opus_paths:
        if path and os.path.exists(path) if path and not path.startswith('opus') else path:
            try:
                discord.opus.load_opus(path)
                print(f"✅ Opus loaded from: {path}")
                return True
            except Exception as e:
                print(f"Failed to load opus from {path}: {e}")
    
    try:
        discord.opus.load_opus('opus')
        print(" Opus loaded (default)")
        return True
    except:
        pass
    
    print(" Could not load Opus library!")
    return False

# Load opus on startup
load_opus()

# Spotify API support (optional - requires credentials in .env)
try:
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials
    SPOTIFY_AVAILABLE = bool(config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET)
    if SPOTIFY_AVAILABLE:
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=config.SPOTIFY_CLIENT_ID,
            client_secret=config.SPOTIFY_CLIENT_SECRET
        ))
        print(" Spotify API initialized")
    else:
        sp = None
        print(" Spotify API credentials not configured")
except ImportError:
    SPOTIFY_AVAILABLE = False
    sp = None
    print(" spotipy not installed (pip install spotipy for Spotify support)")
except Exception as e:
    SPOTIFY_AVAILABLE = False
    sp = None
    print(f" Spotify API initialization failed: {e}")


# Piped instances for YouTube (alternative to Invidious)
PIPED_INSTANCES = [
    'https://pipedapi.kavin.rocks',
    'https://pipedapi.tokhmi.xyz',
    'https://api-piped.mha.fi',
]

async def get_youtube_stream_piped(video_id: str) -> Optional[str]:
    """Get YouTube stream URL using Piped API"""
    import ssl
    
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(connector=connector) as session:
        for instance in PIPED_INSTANCES:
            try:
                url = f"{instance}/streams/{video_id}"
                print(f"  Trying Piped: {instance}")
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        audio_streams = data.get('audioStreams', [])
                        if audio_streams:
                            best = max(audio_streams, key=lambda x: x.get('bitrate', 0))
                            stream_url = best.get('url')
                            if stream_url:
                                print(f"   Got stream from Piped: {instance}")
                                return stream_url
            except Exception as e:
                print(f"  ❌ Piped error: {str(e)[:50]}")
    return None


# Invidious instances for YouTube (bypasses bot detection)
INVIDIOUS_INSTANCES = [
    'https://iv.ggtyler.dev',
    'https://invidious.fdn.fr',
    'https://inv.nadeko.net',
    'https://invidious.private.coffee',
    'https://yt.artemislena.eu',
    'https://invidious.flokinet.to',
]

async def get_youtube_stream_invidious(video_id: str) -> Optional[str]:
    """Get YouTube stream URL using Invidious API"""
    import ssl
    
    # Create SSL context that's more permissive
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(connector=connector) as session:
        for instance in INVIDIOUS_INSTANCES:
            try:
                url = f"{instance}/api/v1/videos/{video_id}"
                print(f"  Trying: {instance}")
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # Get best audio format
                        audio_formats = [f for f in data.get('adaptiveFormats', []) 
                                       if f.get('type', '').startswith('audio')]
                        if audio_formats:
                            # Sort by bitrate, get highest quality
                            best = max(audio_formats, key=lambda x: x.get('bitrate', 0))
                            stream_url = best.get('url')
                            if stream_url:
                                print(f"   Got stream from: {instance}")
                                return stream_url
                        print(f"   No audio formats found")
                    else:
                        print(f"   Status {resp.status}")
            except asyncio.TimeoutError:
                print(f"   Timeout")
            except Exception as e:
                print(f"   Error: {str(e)[:50]}")
                continue
    return None


# Cache management - keep max 3 downloaded songs
MAX_CACHED_SONGS = 3

def cleanup_old_downloads():
    """Keep only the 3 most recent downloaded songs in /tmp"""
    import glob
    import os
    
    # Find all ytdl downloaded files
    files = glob.glob("/tmp/ytdl_*.*")
    
    if len(files) > MAX_CACHED_SONGS:
        # Sort by modification time (oldest first)
        files.sort(key=lambda x: os.path.getmtime(x))
        
        # Delete oldest files to keep only MAX_CACHED_SONGS
        files_to_delete = files[:len(files) - MAX_CACHED_SONGS]
        for file in files_to_delete:
            try:
                os.remove(file)
                print(f" Cleaned up: {os.path.basename(file)}")
            except Exception as e:
                print(f"Failed to delete {file}: {e}")


# yt-dlp configuration - download audio to avoid streaming 403 errors
YTDL_FORMAT_OPTIONS = {
    'format': 'bestaudio/best',  # More flexible format selection
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': False,
    'no_warnings': False,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'outtmpl': '/tmp/ytdl_%(id)s.%(ext)s',
    'sleep_interval': 3,
    'max_sleep_interval': 10,
    'extractor_args': {
        'youtube': {
            'player_client': ['web', 'mweb'],
        }
    },
}

FFMPEG_OPTIONS = {
    'options': '-vn'
}

# Separate extractor for getting info only
YTDL_SEARCH_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'skip_download': True,
    'extract_flat': 'in_playlist',
    'extractor_args': {
        'youtube': {
            'player_client': ['mweb', 'android'],
        }
    },
}

# Extractor for playlists
YTDL_PLAYLIST_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': False,  # Enable playlist extraction
    'nocheckcertificate': True,
    'ignoreerrors': True,  # Continue on errors
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'skip_download': True,
    'extract_flat': 'in_playlist',
    'extractor_args': {
        'youtube': {
            'player_client': ['mweb', 'android'],
        }
    },
}

ytdl = yt_dlp.YoutubeDL(YTDL_FORMAT_OPTIONS)
ytdl_search = yt_dlp.YoutubeDL(YTDL_SEARCH_OPTIONS)
ytdl_playlist = yt_dlp.YoutubeDL(YTDL_PLAYLIST_OPTIONS)


@dataclass
class Song:
    """Represents a song in the queue"""
    title: str
    url: str  # webpage URL for YouTube, file path for local
    duration: str
    requester: discord.Member
    source_type: str  # 'youtube', 'spotify', 'local'
    thumbnail: Optional[str] = None


class YTDLSource(discord.PCMVolumeTransformer):
    """Audio source using yt-dlp"""
    
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('webpage_url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        """Create an audio source from a URL - tries yt-dlp CLI first"""
        loop = loop or asyncio.get_event_loop()
        
        # Extract video ID from URL
        import re
        video_id_match = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11}).*', url)
        video_id = video_id_match.group(1) if video_id_match else 'unknown'
        
        # Try yt-dlp CLI first (works on your local Mac)
        print(f"🎵 Downloading with yt-dlp CLI: {video_id}")
        try:
            import subprocess
            import glob
            
            output_template = f"/tmp/ytdl_{video_id}.%(ext)s"
            
            # Run yt-dlp CLI command
            cmd = [
                'yt-dlp',
                '-f', 'bestaudio/best',
                '-x',
                '--audio-format', 'mp3',
                '-o', output_template,
                url
            ]
            
            print(f"  Running yt-dlp...")
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            )
            
            if result.returncode == 0:
                # Find the downloaded file
                matching_files = glob.glob(f"/tmp/ytdl_{video_id}.*")
                if matching_files:
                    filename = matching_files[0]
                    print(f"   Downloaded: {filename}")
                    
                    # Cleanup old downloads to keep cache under control
                    await loop.run_in_executor(None, cleanup_old_downloads)
                    
                    # Get metadata
                    data = await loop.run_in_executor(
                        None,
                        lambda: ytdl_search.extract_info(url, download=False)
                    )
                    if 'entries' in data:
                        data = data['entries'][0] if data['entries'] else None
                    
                    if data:
                        source = discord.FFmpegPCMAudio(filename, options='-vn')
                        return cls(source, data=data)
            else:
                print(f"   yt-dlp CLI failed: {result.stderr[:200]}")
        except Exception as e:
            print(f"   CLI error: {e}")
        
        # Fallback: Try Piped/Invidious proxies
        if video_id_match:
            print(f"🔍 Trying YouTube proxies for video ID: {video_id}")
            
            # Try Piped
            stream_url = await get_youtube_stream_piped(video_id)
            
            # Try Invidious if Piped fails
            if not stream_url:
                stream_url = await get_youtube_stream_invidious(video_id)
            
            if stream_url:
                # Get video info for metadata
                try:
                    data = await loop.run_in_executor(
                        None,
                        lambda: ytdl_search.extract_info(url, download=False)
                    )
                    
                    if 'entries' in data:
                        data = data['entries'][0] if data['entries'] else None
                    
                    if data:
                        # Stream directly from proxy URL
                        print(f" Streaming from proxy")
                        source = discord.FFmpegPCMAudio(
                            stream_url,
                            before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                            options='-vn'
                        )
                        return cls(source, data=data)
                except Exception as e:
                    print(f"Proxy metadata error: {e}")
        
        raise Exception("All YouTube download methods failed")


class MusicControlView(View):
    """Interactive button controls for music player"""
    
    def __init__(self, bot, guild_id):
        super().__init__(timeout=None)  # Persistent buttons
        self.bot = bot
        self.guild_id = guild_id
    
    def get_player(self):
        """Get the music player for this guild"""
        cog = self.bot.get_cog('MusicCog')
        if cog:
            return cog.get_player(self.bot.get_guild(self.guild_id))
        return None
    
    @discord.ui.button(label="⏮️", style=discord.ButtonStyle.secondary, custom_id="previous")
    async def previous_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("⏮️ Previous song feature coming soon!", ephemeral=True)
    
    @discord.ui.button(label="⏯️", style=discord.ButtonStyle.primary, custom_id="pause_resume")
    async def pause_resume_button(self, interaction: discord.Interaction, button: Button):
        voice_client = interaction.guild.voice_client
        if not voice_client:
            await interaction.response.send_message("❌ Not connected to voice!", ephemeral=True)
            return
        
        if voice_client.is_playing():
            voice_client.pause()
            await interaction.response.send_message("⏸️ Paused!", ephemeral=True)
        elif voice_client.is_paused():
            voice_client.resume()
            await interaction.response.send_message("▶️ Resumed!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
    
    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.secondary, custom_id="skip")
    async def skip_button(self, interaction: discord.Interaction, button: Button):
        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_playing():
            await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
            return
        
        player = self.get_player()
        if player:
            player.loop = False
        
        await interaction.response.defer()
        voice_client.stop()
        await asyncio.sleep(0.5)
        
        if player and player.current:
            cog = self.bot.get_cog('MusicCog')
            embed = cog.create_now_playing_embed(player.current)
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send("⏭️ Skipped! No more songs.")
    
    @discord.ui.button(label="🔊", style=discord.ButtonStyle.secondary, custom_id="volume_up")
    async def volume_up_button(self, interaction: discord.Interaction, button: Button):
        player = self.get_player()
        if not player:
            await interaction.response.send_message("❌ No player found!", ephemeral=True)
            return
        
        new_volume = min(player.volume + 0.1, 1.0)
        player.volume = new_volume
        
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.source:
            voice_client.source.volume = new_volume
        
        await interaction.response.send_message(f"🔊 Volume: {int(new_volume * 100)}%", ephemeral=True)
    
    @discord.ui.button(label="🔉", style=discord.ButtonStyle.secondary, custom_id="volume_down")
    async def volume_down_button(self, interaction: discord.Interaction, button: Button):
        player = self.get_player()
        if not player:
            await interaction.response.send_message("❌ No player found!", ephemeral=True)
            return
        
        new_volume = max(player.volume - 0.1, 0.0)
        player.volume = new_volume
        
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.source:
            voice_client.source.volume = new_volume
        
        await interaction.response.send_message(f"🔉 Volume: {int(new_volume * 100)}%", ephemeral=True)
    
    @discord.ui.button(label="🔀", style=discord.ButtonStyle.secondary, custom_id="shuffle", row=1)
    async def shuffle_button(self, interaction: discord.Interaction, button: Button):
        import random
        player = self.get_player()
        if not player:
            await interaction.response.send_message("❌ No player found!", ephemeral=True)
            return
        
        if len(player.queue) < 2:
            await interaction.response.send_message("❌ Not enough songs to shuffle!", ephemeral=True)
            return
        
        queue_list = list(player.queue)
        random.shuffle(queue_list)
        player.queue = deque(queue_list)
        player.preloaded_sources.clear()  # Clear preloaded cache since queue order changed
        
        # Preload the new next song
        asyncio.create_task(player.preload_next_song())
        
        await interaction.response.send_message("🔀 Queue shuffled!", ephemeral=True)
    
    @discord.ui.button(label="⏹️", style=discord.ButtonStyle.danger, custom_id="stop", row=1)
    async def stop_button(self, interaction: discord.Interaction, button: Button):
        player = self.get_player()
        if player:
            player.queue.clear()
            player.current = None
            player.loop = False
            player.loop_queue = False
            player.pending_playlist = None
            player.preloaded_sources.clear()  # Clear preloaded cache
        
        voice_client = interaction.guild.voice_client
        if voice_client:
            voice_client.stop()
        
        await interaction.response.send_message("⏹️ Stopped!", ephemeral=True)
    
    @discord.ui.button(label="📜", style=discord.ButtonStyle.secondary, custom_id="queue", row=1)
    async def queue_button(self, interaction: discord.Interaction, button: Button):
        player = self.get_player()
        if not player:
            await interaction.response.send_message("❌ No player found!", ephemeral=True)
            return
        
        if not player.current and not player.queue:
            await interaction.response.send_message("📭 Queue is empty!", ephemeral=True)
            return
        
        embed = discord.Embed(title="🎶 Music Queue", color=discord.Color.blurple())
        
        if player.current:
            embed.add_field(
                name="Now Playing",
                value=f"**{player.current.title}** [{player.current.duration}]",
                inline=False
            )
        
        if player.queue:
            queue_list = []
            for i, song in enumerate(list(player.queue)[:10], 1):
                queue_list.append(f"`{i}.` **{song.title}** [{song.duration}]")
            
            if len(player.queue) > 10:
                queue_list.append(f"\n*...and {len(player.queue) - 10} more*")
            
            embed.add_field(name="Up Next", value="\n".join(queue_list), inline=False)
        
        status = []
        if player.loop:
            status.append("🔂 Loop: Song")
        if player.loop_queue:
            status.append("🔁 Loop: Queue")
        if status:
            embed.set_footer(text=" | ".join(status))
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


class MusicPlayer:
    """Music player for a guild"""
    
    def __init__(self, bot, guild):
        self.bot = bot
        self.guild = guild
        self.queue = deque()
        self.current: Optional[Song] = None
        self.volume = 0.5
        self.loop = False
        self.loop_queue = False
        self.pending_playlist = None  # For just-in-time playlist loading
        self.preloaded_sources = {}  # Cache for pre-downloaded audio sources
        self._preload_task = None  # Background preload task

    async def preload_next_song(self):
        """Preload the next song in queue while current is playing"""
        if not self.queue:
            return
        
        # Get the next song without removing it
        next_song = self.queue[0]
        song_key = f"{next_song.url}_{id(next_song)}"
        
        # Skip if already preloaded
        if song_key in self.preloaded_sources:
            return
        
        try:
            print(f"🔄 Preloading: {next_song.title}")
            
            if next_song.source_type == 'local':
                # Local files don't need preloading
                return
            elif next_song.source_type == 'spotify' and next_song.url.startswith('spotify:search:'):
                # Spotify song - search on YouTube first
                search_query = next_song.url.replace('spotify:search:', '')
                cog = self.bot.get_cog('MusicCog')
                if cog:
                    yt_song = await cog.process_youtube(search_query, next_song.requester)
                    if yt_song:
                        # Update the song in queue
                        next_song.url = yt_song.url
                        next_song.title = yt_song.title
                        next_song.duration = yt_song.duration
                        next_song.thumbnail = yt_song.thumbnail
                
                # Now preload the stream
                source = await YTDLSource.from_url(
                    next_song.url,
                    loop=self.bot.loop,
                    stream=True
                )
                self.preloaded_sources[song_key] = source
                print(f"✅ Preloaded: {next_song.title}")
            else:
                # YouTube - preload stream
                source = await YTDLSource.from_url(
                    next_song.url,
                    loop=self.bot.loop,
                    stream=True
                )
                self.preloaded_sources[song_key] = source
                print(f"✅ Preloaded: {next_song.title}")
                
        except Exception as e:
            print(f"Preload error: {e}")

    async def play_next(self):
        """Play the next song in queue"""
        if self.loop and self.current:
            self.queue.appendleft(self.current)
        elif self.loop_queue and self.current:
            self.queue.append(self.current)
        
        # Load next song from pending playlist if queue is empty
        if not self.queue and self.pending_playlist:
            await self.load_next_from_playlist()
        
        if not self.queue:
            self.current = None
            self.preloaded_sources.clear()  # Clear preload cache
            print("Queue is empty, nothing to play")
            return
        
        self.current = self.queue.popleft()
        song_key = f"{self.current.url}_{id(self.current)}"
        
        # Safety check
        if not self.current:
            print("ERROR: Popped None from queue!")
            await self.play_next()
            return
        
        # Load next song in background while current plays
        if self.pending_playlist:
            asyncio.create_task(self.load_next_from_playlist())
        
        print(f"Playing from queue: {self.current.title} (type: {self.current.source_type})")
        
        voice_client = self.guild.voice_client
        if not voice_client:
            print("No voice client!")
            return
        
        try:
            # Check if we have a preloaded source
            if song_key in self.preloaded_sources:
                print(f"🚀 Using preloaded source for: {self.current.title}")
                source = self.preloaded_sources.pop(song_key)
                source.volume = self.volume
            elif self.current.source_type == 'local':
                # Local file - direct FFmpeg
                source = discord.FFmpegPCMAudio(self.current.url)
                source = discord.PCMVolumeTransformer(source, volume=self.volume)
            elif self.current.source_type == 'spotify' and self.current.url.startswith('spotify:search:'):
                # Spotify song needs to be searched on YouTube first
                search_query = self.current.url.replace('spotify:search:', '')
                print(f"🔍 Searching YouTube for: {search_query}")
                
                # Get the cog to use process_youtube
                cog = self.bot.get_cog('MusicCog')
                if cog:
                    yt_song = await cog.process_youtube(search_query, self.current.requester)
                    if yt_song:
                        # Update current song with actual YouTube URL
                        self.current.url = yt_song.url
                        self.current.title = yt_song.title
                        self.current.duration = yt_song.duration
                        self.current.thumbnail = yt_song.thumbnail
                
                # Now get the stream
                source = await YTDLSource.from_url(
                    self.current.url, 
                    loop=self.bot.loop, 
                    stream=True
                )
                source.volume = self.volume
            else:
                # YouTube/Spotify - use yt-dlp to get stream
                source = await YTDLSource.from_url(
                    self.current.url, 
                    loop=self.bot.loop, 
                    stream=True
                )
                source.volume = self.volume
            
            def after_playing(error):
                if error:
                    print(f"Player error: {error}")
                coro = self.play_next()
                fut = asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
                try:
                    fut.result()
                except Exception as e:
                    print(f"Error in play_next: {e}")
            
            voice_client.play(source, after=after_playing)
            print(f"▶️ Now playing: {self.current.title}")
            
            # Start preloading the next song in background
            if self.queue:
                asyncio.create_task(self.preload_next_song())
            
        except Exception as e:
            print(f"Error playing song: {e}")
            import traceback
            traceback.print_exc()
            # Try next song
            await self.play_next()
    
    async def load_next_from_playlist(self):
        """Load the next song from pending playlist"""
        if not self.pending_playlist:
            return
        
        try:
            playlist = self.pending_playlist
            idx = playlist['current_index']
            
            # Check if Spotify or YouTube
            if playlist.get('is_spotify'):
                tracks = playlist['tracks']
                if idx >= len(tracks):
                    # No more tracks
                    self.pending_playlist = None
                    return
                
                item = tracks[idx]
                if playlist['is_album']:
                    track, artist_name = item
                    search_query = f"{track['name']} {artist_name}"
                else:
                    track = item.get('track')
                    if not track or not track.get('name'):
                        playlist['current_index'] += 1
                        return
                    search_query = f"{track['name']} {track['artists'][0]['name']}"
                
                print(f"🔍 Loading next Spotify song: {search_query}")
                # Import needed to call process_youtube
                from music_bot import MusicCog
                cog = self.bot.get_cog('MusicCog')
                if cog:
                    song = await cog.process_youtube(search_query, playlist['requester'])
                    if song:
                        song.source_type = 'spotify'
                        self.queue.append(song)
                        print(f"  ✅ Added: {song.title}")
            
            else:
                # YouTube playlist
                entries = playlist['entries']
                if idx >= len(entries):
                    # No more entries
                    self.pending_playlist = None
                    return
                
                entry = entries[idx]
                if not entry:
                    playlist['current_index'] += 1
                    return
                
                video_id = entry.get('id')
                title = entry.get('title', 'Unknown')
                duration = entry.get('duration', 0)
                video_url = entry.get('webpage_url') or entry.get('url') or f"https://www.youtube.com/watch?v={video_id}"
                
                # Import to get format_duration
                cog = self.bot.get_cog('MusicCog')
                duration_str = cog.format_duration(duration) if cog else "Unknown"
                
                song = Song(
                    title=title,
                    url=video_url,
                    duration=duration_str,
                    requester=playlist['requester'],
                    source_type='youtube',
                    thumbnail=entry.get('thumbnail')
                )
                self.queue.append(song)
                print(f"🔍 Loading next YouTube song: {title}")
            
            # Increment index for next time
            playlist['current_index'] += 1
            
        except Exception as e:
            print(f"Error loading next playlist song: {e}")
            import traceback
            traceback.print_exc()


class MusicCog(commands.Cog):
    """Music commands cog"""
    
    def __init__(self, bot):
        self.bot = bot
        self.players = {}
    
    def get_player(self, guild) -> MusicPlayer:
        if guild.id not in self.players:
            self.players[guild.id] = MusicPlayer(self.bot, guild)
        return self.players[guild.id]
    
    async def ensure_voice(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.voice:
            await interaction.response.send_message(" You need to be in a voice channel!", ephemeral=True)
            return False
        
        if not interaction.guild.voice_client:
            await interaction.user.voice.channel.connect()
        elif interaction.guild.voice_client.channel != interaction.user.voice.channel:
            await interaction.guild.voice_client.move_to(interaction.user.voice.channel)
        
        return True

    @app_commands.command(name="play", description="Play a song from YouTube, Spotify, or upload a local file")
    @app_commands.describe(
        query="YouTube/Spotify URL or search query",
        file="Upload an audio file (mp3, wav, ogg, flac, etc.)"
    )
    async def play(self, interaction: discord.Interaction, query: str = None, file: discord.Attachment = None):
        if not await self.ensure_voice(interaction):
            return
        
        await interaction.response.defer()
        player = self.get_player(interaction.guild)
        
        try:
            songs_added = []
            
            # Local file upload
            if file:
                ext = os.path.splitext(file.filename)[1].lower()
                if ext not in config.SUPPORTED_FORMATS:
                    await interaction.followup.send(f" Unsupported format. Supported: {', '.join(config.SUPPORTED_FORMATS)}")
                    return
                
                temp_dir = tempfile.gettempdir()
                filepath = os.path.join(temp_dir, f"discord_music_{interaction.guild.id}_{file.filename}")
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(file.url) as resp:
                        with open(filepath, 'wb') as f:
                            f.write(await resp.read())
                
                song = Song(
                    title=file.filename,
                    url=filepath,
                    duration="Unknown",
                    requester=interaction.user,
                    source_type='local'
                )
                songs_added.append(song)
            
            # Spotify URL (requires API credentials)
            elif query and ('spotify.com' in query or 'spotify:' in query):
                if not SPOTIFY_AVAILABLE:
                    await interaction.followup.send(" Spotify support not configured. Please add SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to your .env file, or use YouTube URLs instead.")
                    return
                
                # Check if it's a playlist or album
                if 'playlist' in query or 'album' in query:
                    all_songs, total_count = await self.process_spotify_playlist_fast(query, interaction.user)
                    songs_added = all_songs
                else:
                    # Single track
                    songs_added = await self.process_spotify(query, interaction.user)
            
            # YouTube URL or search
            elif query:
                print(f"Processing query: {query}")
                # Check if it's a playlist URL
                if 'list=' in query:
                    print("Detected playlist URL")
                    # Load ALL songs to queue immediately (metadata only), download on-demand
                    all_songs, total_count = await self.process_youtube_playlist_fast(query, interaction.user)
                    songs_added = all_songs
                else:
                    song = await self.process_youtube(query, interaction.user)
                    print(f"Got song: {song}")
                    if song:
                        songs_added.append(song)
                    else:
                        print("Song was None!")
            
            else:
                await interaction.followup.send(" Please provide a URL, search query, or upload a file!")
                return
            
            if not songs_added:
                await interaction.followup.send(" No songs found!")
                return
            
            # Add to queue
            for song in songs_added:
                player.queue.append(song)
            
            # Start playing if not already
            vc = interaction.guild.voice_client
            if vc and not vc.is_playing() and not vc.is_paused():
                await player.play_next()
                if player.current:
                    embed = self.create_now_playing_embed(player.current)
                    view = MusicControlView(self.bot, interaction.guild.id)
                    await interaction.followup.send(embed=embed, view=view)
                else:
                    await interaction.followup.send("❌ Failed to play the song.")
            else:
                if len(songs_added) == 1:
                    embed = discord.Embed(
                        title="🎵 Added to Queue",
                        description=f"**{songs_added[0].title}**",
                        color=discord.Color.green()
                    )
                    embed.add_field(name="Position", value=str(len(player.queue)))
                else:
                    embed = discord.Embed(
                        title="🎵 Added to Queue",
                        description=f"Added **{len(songs_added)}** songs",
                        color=discord.Color.green()
                    )
                    # Check if there's a background task loading more
                    if hasattr(player, '_loading_playlist') and player._loading_playlist:
                        embed.set_footer(text="⏳ Loading more songs in background...")
                await interaction.followup.send(embed=embed)
                
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)}")
            print(f"Play error: {e}")
            import traceback
            traceback.print_exc()

    async def process_youtube(self, query: str, requester: discord.Member) -> Optional[Song]:
        """Process YouTube URL or search query - just get metadata"""
        loop = asyncio.get_event_loop()
        
        try:
            print(f"Searching for: {query}")
            # Use search extractor for getting info
            search_query = f"ytsearch:{query}" if not query.startswith('http') else query
            data = await loop.run_in_executor(
                None, 
                lambda: ytdl_search.extract_info(search_query, download=False)
            )
            
            if not data:
                print("No data returned from yt-dlp")
                return None
            
            # Handle search results
            if 'entries' in data:
                if not data['entries']:
                    print("No entries in search results")
                    return None
                data = data['entries'][0]
                if not data:
                    print("First entry is None")
                    return None
            
            title = data.get('title', 'Unknown')
            # Use webpage_url for playing later, fall back to original URL
            url = data.get('webpage_url') or data.get('original_url') or data.get('url') or query
            
            print(f"Found: {title} -> {url}")
            
            return Song(
                title=title,
                url=url,
                duration=self.format_duration(data.get('duration', 0)),
                requester=requester,
                source_type='youtube',
                thumbnail=data.get('thumbnail')
            )
            
        except Exception as e:
            print(f"YouTube processing error: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    async def process_youtube_playlist_fast(self, url: str, requester: discord.Member) -> tuple[list[Song], int]:
        """Add all playlist songs to queue with metadata only - download happens on-demand"""
        loop = asyncio.get_event_loop()
        songs = []
        
        try:
            print(f"Loading playlist metadata: {url}")
            data = await loop.run_in_executor(
                None,
                lambda: ytdl_playlist.extract_info(url, download=False)
            )
            
            if not data:
                return songs, 0
            
            if 'entries' not in data:
                song = await self.process_youtube(url, requester)
                if song:
                    songs.append(song)
                return songs, 1
            
            entries = data.get('entries', [])
            total_count = len(entries)
            playlist_title = data.get('title', 'Playlist')
            print(f"Found playlist: {playlist_title} with {total_count} videos")
            
            # Add all songs to queue (metadata only, up to 50)
            for entry in entries[:50]:
                if not entry:
                    continue
                
                try:
                    video_id = entry.get('id')
                    title = entry.get('title', 'Unknown')
                    duration = entry.get('duration', 0)
                    video_url = entry.get('webpage_url') or entry.get('url') or f"https://www.youtube.com/watch?v={video_id}"
                    
                    song = Song(
                        title=title,
                        url=video_url,
                        duration=self.format_duration(duration),
                        requester=requester,
                        source_type='youtube',
                        thumbnail=entry.get('thumbnail')
                    )
                    songs.append(song)
                except Exception as e:
                    print(f"Error processing entry: {e}")
                    continue
            
            print(f"✅ Added {len(songs)} songs to queue (will download on-demand)")
            
        except Exception as e:
            print(f"Playlist processing error: {e}")
        
        return songs, total_count
    
    async def process_spotify_playlist_fast(self, url: str, requester: discord.Member) -> tuple[list[Song], int]:
        """Add all Spotify playlist songs to queue with track names - search happens on-demand"""
        songs = []
        total_count = 0
        
        if not SPOTIFY_AVAILABLE:
            return songs, 0
        
        try:
            print(f"Loading Spotify playlist metadata: {url}")
            
            if 'playlist' in url:
                playlist = sp.playlist(url)
                total_count = playlist['tracks']['total']
                results = playlist['tracks']
                all_tracks = results['items']
                
                # Get all tracks
                while results['next']:
                    results = sp.next(results)
                    all_tracks.extend(results['items'])
                
                print(f"  Playlist: {playlist['name']} ({total_count} tracks)")
                
                # Add all tracks to queue (metadata only, up to 50)
                for item in all_tracks[:50]:
                    track = item.get('track')
                    if track and track.get('name'):
                        # Create a special Song object that will be searched later
                        search_query = f"{track['name']} {track['artists'][0]['name']}"
                        song = Song(
                            title=search_query,  # Store search query as title temporarily
                            url=f"spotify:search:{search_query}",  # Special URL marker
                            duration="Unknown",
                            requester=requester,
                            source_type='spotify',
                            thumbnail=track.get('album', {}).get('images', [{}])[0].get('url') if track.get('album') else None
                        )
                        songs.append(song)
            
            elif 'album' in url:
                album = sp.album(url)
                total_count = album['total_tracks']
                artist_name = album['artists'][0]['name']
                
                print(f"  Album: {album['name']} ({total_count} tracks)")
                
                # Add all tracks to queue (metadata only, up to 50)
                for track in album['tracks']['items'][:50]:
                    search_query = f"{track['name']} {artist_name}"
                    song = Song(
                        title=search_query,
                        url=f"spotify:search:{search_query}",
                        duration="Unknown",
                        requester=requester,
                        source_type='spotify',
                        thumbnail=album.get('images', [{}])[0].get('url') if album.get('images') else None
                    )
                    songs.append(song)
            
            print(f"✅ Added {len(songs)} songs to queue (will search on-demand)")
            
        except Exception as e:
            print(f"Spotify playlist processing error: {e}")
        
        return songs, total_count
    
    async def process_youtube_playlist_initial(self, url: str, requester: discord.Member) -> tuple[list[Song], int, list]:
        """Process first song of YouTube playlist only, store rest for later"""
        loop = asyncio.get_event_loop()
        songs = []
        total_count = 0
        all_entries = []
        
        try:
            print(f"Processing playlist (first song only): {url}")
            data = await loop.run_in_executor(
                None,
                lambda: ytdl_playlist.extract_info(url, download=False)
            )
            
            if not data:
                return songs, 0, []
            
            if 'entries' not in data:
                song = await self.process_youtube(url, requester)
                if song:
                    songs.append(song)
                return songs, 1, []
            
            all_entries = data.get('entries', [])
            total_count = len(all_entries)
            playlist_title = data.get('title', 'Playlist')
            print(f"Found playlist: {playlist_title} with {total_count} videos")
            
            # Process only first song
            first_entry = all_entries[0] if all_entries else None
            if first_entry:
                try:
                    video_id = first_entry.get('id')
                    title = first_entry.get('title', 'Unknown')
                    duration = first_entry.get('duration', 0)
                    video_url = first_entry.get('webpage_url') or first_entry.get('url') or f"https://www.youtube.com/watch?v={video_id}"
                    
                    song = Song(
                        title=title,
                        url=video_url,
                        duration=self.format_duration(duration),
                        requester=requester,
                        source_type='youtube',
                        thumbnail=first_entry.get('thumbnail')
                    )
                    songs.append(song)
                except Exception as e:
                    print(f"Error processing entry: {e}")
            
        except Exception as e:
            print(f"Playlist processing error: {e}")
        
        return songs, total_count, all_entries
    
    async def process_youtube_playlist(self, url: str, requester: discord.Member) -> list[Song]:
        """Process YouTube playlist and return list of songs"""
        loop = asyncio.get_event_loop()
        songs = []
        
        try:
            print(f"Processing playlist: {url}")
            data = await loop.run_in_executor(
                None,
                lambda: ytdl_playlist.extract_info(url, download=False)
            )
            
            if not data:
                print("No data returned from playlist")
                return songs
            
            # Check if it's a playlist
            if 'entries' not in data:
                # Single video, not a playlist
                song = await self.process_youtube(url, requester)
                if song:
                    songs.append(song)
                return songs
            
            # Process playlist entries
            entries = data.get('entries', [])
            playlist_title = data.get('title', 'Playlist')
            print(f"Found playlist: {playlist_title} with {len(entries)} videos")
            
            # Limit to first 50 videos to avoid spam
            for entry in entries[:50]:
                if not entry:
                    continue
                
                try:
                    # Extract video info
                    video_id = entry.get('id')
                    title = entry.get('title', 'Unknown')
                    duration = entry.get('duration', 0)
                    
                    # Construct URL
                    video_url = entry.get('webpage_url') or entry.get('url') or f"https://www.youtube.com/watch?v={video_id}"
                    
                    song = Song(
                        title=title,
                        url=video_url,
                        duration=self.format_duration(duration),
                        requester=requester,
                        source_type='youtube',
                        thumbnail=entry.get('thumbnail')
                    )
                    songs.append(song)
                    
                except Exception as e:
                    print(f"Error processing playlist entry: {e}")
                    continue
            
            print(f"Successfully processed {len(songs)} songs from playlist")
            
        except Exception as e:
            print(f"Playlist processing error: {e}")
            import traceback
            traceback.print_exc()
        
        return songs

    async def process_youtube_background(self, entries: list, requester: discord.Member, player: MusicPlayer):
        """Process remaining YouTube playlist entries in background (instant - uses pre-fetched data!)"""
        try:
            print(f"🔄 Processing {len(entries)} remaining songs in background...")
            
            for entry in entries:
                if not entry:
                    continue
                
                try:
                    video_id = entry.get('id')
                    title = entry.get('title', 'Unknown')
                    duration = entry.get('duration', 0)
                    video_url = entry.get('webpage_url') or entry.get('url') or f"https://www.youtube.com/watch?v={video_id}"
                    
                    song = Song(
                        title=title,
                        url=video_url,
                        duration=self.format_duration(duration),
                        requester=requester,
                        source_type='youtube',
                        thumbnail=entry.get('thumbnail')
                    )
                    player.queue.append(song)
                    print(f"  ✅ Added: {title}")
                except Exception as e:
                    print(f"Error processing entry: {e}")
                    continue
            
            print(f"✅ Finished loading playlist ({len(player.queue)} total songs in queue)")
            
        except Exception as e:
            print(f"Background playlist loading error: {e}")
        finally:
            player._loading_playlist = False

    async def process_playlist_background(self, url: str, requester: discord.Member, player: MusicPlayer, guild: discord.Guild, source: str, total_count: int):
        """Process remaining playlist songs in background"""
        try:
            print(f"🔄 Loading remaining songs from {source} playlist in background...")
            
            if source == 'youtube':
                loop = asyncio.get_event_loop()
                data = await loop.run_in_executor(
                    None,
                    lambda: ytdl_playlist.extract_info(url, download=False)
                )
                
                if data and 'entries' in data:
                    entries = data['entries'][3:50]  # Skip first 3, limit to 50 total
                    
                    for entry in entries:
                        if not entry:
                            continue
                        
                        try:
                            video_id = entry.get('id')
                            title = entry.get('title', 'Unknown')
                            duration = entry.get('duration', 0)
                            video_url = entry.get('webpage_url') or entry.get('url') or f"https://www.youtube.com/watch?v={video_id}"
                            
                            song = Song(
                                title=title,
                                url=video_url,
                                duration=self.format_duration(duration),
                                requester=requester,
                                source_type='youtube',
                                thumbnail=entry.get('thumbnail')
                            )
                            player.queue.append(song)
                            print(f"  ✅ Added: {title}")
                        except Exception as e:
                            print(f"Error processing entry: {e}")
                            continue
            
            elif source == 'spotify':
                # Get remaining tracks from Spotify
                if 'playlist' in url:
                    playlist = sp.playlist(url)
                    results = playlist['tracks']
                    tracks = results['items']
                    
                    while results['next']:
                        results = sp.next(results)
                        tracks.extend(results['items'])
                    
                    # Process tracks starting from index 3
                    for item in tracks[3:50]:
                        track = item.get('track')
                        if track and track.get('name'):
                            search_query = f"{track['name']} {track['artists'][0]['name']}"
                            print(f"  Searching: {search_query}")
                            song = await self.process_youtube(search_query, requester)
                            if song:
                                song.source_type = 'spotify'
                                player.queue.append(song)
                            await asyncio.sleep(0.2)
                
                elif 'album' in url:
                    album = sp.album(url)
                    artist_name = album['artists'][0]['name']
                    
                    for track in album['tracks']['items'][3:50]:
                        search_query = f"{track['name']} {artist_name}"
                        print(f"  Searching: {search_query}")
                        song = await self.process_youtube(search_query, requester)
                        if song:
                            song.source_type = 'spotify'
                            player.queue.append(song)
                        await asyncio.sleep(0.2)
            
            print(f"✅ Finished loading playlist ({len(player.queue)} total songs in queue)")
            
        except Exception as e:
            print(f"Background playlist loading error: {e}")
        finally:
            player._loading_playlist = False
    
    async def process_spotify_initial(self, url: str, requester: discord.Member) -> tuple[list[Song], int, list]:
        """Process first song of Spotify playlist/album only, store rest for later"""
        songs = []
        total_count = 0
        remaining_tracks = []
        
        if not SPOTIFY_AVAILABLE:
            return songs, 0, []
        
        try:
            print(f"Processing Spotify URL (first song only): {url}")
            
            if 'playlist' in url:
                playlist = sp.playlist(url)
                total_count = playlist['tracks']['total']
                results = playlist['tracks']
                all_tracks = results['items']
                
                # Get all tracks
                while results['next']:
                    results = sp.next(results)
                    all_tracks.extend(results['items'])
                
                remaining_tracks = all_tracks[1:50]  # Store all except first
                
                print(f"  Playlist: {playlist['name']} ({total_count} tracks)")
                
                # Process only first track
                if all_tracks:
                    track = all_tracks[0].get('track')
                    if track and track.get('name'):
                        search_query = f"{track['name']} {track['artists'][0]['name']}"
                        print(f"  Searching: {search_query}")
                        song = await self.process_youtube(search_query, requester)
                        if song:
                            song.source_type = 'spotify'
                            songs.append(song)
            
            elif 'album' in url:
                album = sp.album(url)
                total_count = album['total_tracks']
                artist_name = album['artists'][0]['name']
                all_tracks = album['tracks']['items']
                
                remaining_tracks = [(track, artist_name) for track in all_tracks[1:50]]  # Store all except first
                
                print(f"  Album: {album['name']} ({total_count} tracks)")
                
                # Process only first track
                if all_tracks:
                    track = all_tracks[0]
                    search_query = f"{track['name']} {artist_name}"
                    print(f"  Searching: {search_query}")
                    song = await self.process_youtube(search_query, requester)
                    if song:
                        song.source_type = 'spotify'
                        songs.append(song)
        
        except Exception as e:
            print(f"Spotify initial processing error: {e}")
        
        return songs, total_count, remaining_tracks

    async def process_spotify(self, url: str, requester: discord.Member) -> list[Song]:
        """Process Spotify single track"""
        songs = []
        
        if not SPOTIFY_AVAILABLE:
            print("❌ Spotify API not configured. Please add credentials to .env file.")
            return songs
        
        try:
            print(f"Processing Spotify track: {url}")
            
            if 'track' in url:
                track = sp.track(url)
                search_query = f"{track['name']} {track['artists'][0]['name']}"
                print(f"  Track: {search_query}")
                song = await self.process_youtube(search_query, requester)
                if song:
                    song.source_type = 'spotify'
                    songs.append(song)
        
        except Exception as e:
            print(f"Spotify error: {e}")
            import traceback
            traceback.print_exc()
        
        return songs
    
    def format_duration(self, seconds) -> str:
        if not seconds:
            return "Unknown"
        seconds = int(seconds)
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    def create_now_playing_embed(self, song: Song) -> discord.Embed:
        source_emoji = {'youtube': '🔴', 'spotify': '💚', 'local': '📁'}.get(song.source_type, '🎵')
        
        embed = discord.Embed(
            title=f"{source_emoji} Now Playing",
            description=f"**{song.title}**",
            color=discord.Color.blurple()
        )
        embed.add_field(name="Duration", value=song.duration, inline=True)
        embed.add_field(name="Requested by", value=song.requester.mention, inline=True)
        
        if song.thumbnail:
            embed.set_thumbnail(url=song.thumbnail)
        
        return embed

    @app_commands.command(name="skip", description="Skip the current song")
    async def skip(self, interaction: discord.Interaction):
        if not interaction.guild.voice_client or not interaction.guild.voice_client.is_playing():
            await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
            return
        
        player = self.get_player(interaction.guild)
        player.loop = False
        
        # Defer the response since we need to wait for the next song to start
        await interaction.response.defer()
        
        # Stop current song (this will trigger play_next)
        interaction.guild.voice_client.stop()
        
        # Wait a moment for the next song to start playing
        await asyncio.sleep(0.5)
        
        # Send now playing embed for the new song
        if player.current:
            embed = self.create_now_playing_embed(player.current)
            view = MusicControlView(self.bot, interaction.guild.id)
            await interaction.followup.send(embed=embed, view=view)
        else:
            await interaction.followup.send("⏭️ Skipped! No more songs in queue.")

    @app_commands.command(name="stop", description="Stop playback and clear the queue")
    async def stop(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild)
        player.queue.clear()
        player.current = None
        player.loop = False
        player.loop_queue = False
        player.preloaded_sources.clear()  # Clear preloaded cache
        
        if interaction.guild.voice_client:
            interaction.guild.voice_client.stop()
        
        await interaction.response.send_message("⏹️ Stopped and cleared queue!")

    @app_commands.command(name="pause", description="Pause the current song")
    async def pause(self, interaction: discord.Interaction):
        if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.pause()
            await interaction.response.send_message("⏸️ Paused!")
        else:
            await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)

    @app_commands.command(name="resume", description="Resume the paused song")
    async def resume(self, interaction: discord.Interaction):
        if interaction.guild.voice_client and interaction.guild.voice_client.is_paused():
            interaction.guild.voice_client.resume()
            await interaction.response.send_message("▶️ Resumed!")
        else:
            await interaction.response.send_message("❌ Nothing is paused!", ephemeral=True)

    @app_commands.command(name="queue", description="Show the current queue")
    async def queue(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild)
        
        if not player.current and not player.queue:
            await interaction.response.send_message("📭 Queue is empty!", ephemeral=True)
            return
        
        embed = discord.Embed(title="🎶 Music Queue", color=discord.Color.blurple())
        
        if player.current:
            embed.add_field(
                name="Now Playing",
                value=f"**{player.current.title}** [{player.current.duration}]",
                inline=False
            )
        
        if player.queue:
            queue_list = []
            for i, song in enumerate(list(player.queue)[:10], 1):
                queue_list.append(f"`{i}.` **{song.title}** [{song.duration}]")
            
            if len(player.queue) > 10:
                queue_list.append(f"\n*...and {len(player.queue) - 10} more*")
            
            embed.add_field(name="Up Next", value="\n".join(queue_list), inline=False)
        
        status = []
        if player.loop:
            status.append("🔂 Loop: Song")
        if player.loop_queue:
            status.append("🔁 Loop: Queue")
        if status:
            embed.set_footer(text=" | ".join(status))
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="nowplaying", description="Show the currently playing song")
    async def nowplaying(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild)
        
        if not player.current:
            await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
            return
        
        embed = self.create_now_playing_embed(player.current)
        view = MusicControlView(self.bot, interaction.guild.id)
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="volume", description="Set the volume (0-100)")
    @app_commands.describe(level="Volume level (0-100)")
    async def volume(self, interaction: discord.Interaction, level: int):
        if level < 0 or level > 100:
            await interaction.response.send_message("❌ Volume must be between 0 and 100!", ephemeral=True)
            return
        
        player = self.get_player(interaction.guild)
        player.volume = level / 100
        
        if interaction.guild.voice_client and interaction.guild.voice_client.source:
            interaction.guild.voice_client.source.volume = player.volume
        
        await interaction.response.send_message(f"🔊 Volume set to **{level}%**")

    @app_commands.command(name="loop", description="Toggle loop mode")
    @app_commands.describe(mode="Loop mode: song, queue, or off")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Song", value="song"),
        app_commands.Choice(name="Queue", value="queue"),
        app_commands.Choice(name="Off", value="off"),
    ])
    async def loop(self, interaction: discord.Interaction, mode: str):
        player = self.get_player(interaction.guild)
        
        if mode == "song":
            player.loop = True
            player.loop_queue = False
            await interaction.response.send_message("🔂 Looping current song!")
        elif mode == "queue":
            player.loop = False
            player.loop_queue = True
            await interaction.response.send_message("🔁 Looping queue!")
        else:
            player.loop = False
            player.loop_queue = False
            await interaction.response.send_message("➡️ Loop disabled!")

    @app_commands.command(name="shuffle", description="Shuffle the queue")
    async def shuffle(self, interaction: discord.Interaction):
        import random
        player = self.get_player(interaction.guild)
        
        if len(player.queue) < 2:
            await interaction.response.send_message("❌ Not enough songs to shuffle!", ephemeral=True)
            return
        
        queue_list = list(player.queue)
        random.shuffle(queue_list)
        player.queue = deque(queue_list)
        player.preloaded_sources.clear()  # Clear preloaded cache since queue order changed
        
        # Preload the new next song
        asyncio.create_task(player.preload_next_song())
        
        await interaction.response.send_message("🔀 Queue shuffled!")

    @app_commands.command(name="clear", description="Clear the queue")
    async def clear(self, interaction: discord.Interaction):
        player = self.get_player(interaction.guild)
        player.queue.clear()
        player.preloaded_sources.clear()  # Clear preloaded cache
        await interaction.response.send_message("🗑️ Queue cleared!")

    @app_commands.command(name="remove", description="Remove a song from the queue")
    @app_commands.describe(position="Position in queue to remove")
    async def remove(self, interaction: discord.Interaction, position: int):
        player = self.get_player(interaction.guild)
        
        if position < 1 or position > len(player.queue):
            await interaction.response.send_message(f"❌ Invalid position! Queue has {len(player.queue)} songs.", ephemeral=True)
            return
        
        queue_list = list(player.queue)
        removed = queue_list.pop(position - 1)
        player.queue = deque(queue_list)
        
        await interaction.response.send_message(f"🗑️ Removed **{removed.title}** from queue!")

    @app_commands.command(name="disconnect", description="Disconnect the bot from voice channel")
    async def disconnect(self, interaction: discord.Interaction):
        if interaction.guild.voice_client:
            player = self.get_player(interaction.guild)
            player.queue.clear()
            player.current = None
            player.preloaded_sources.clear()  # Clear preloaded cache
            await interaction.guild.voice_client.disconnect()
            await interaction.response.send_message("👋 Disconnected!")
        else:
            await interaction.response.send_message("❌ Not connected to a voice channel!", ephemeral=True)

    @app_commands.command(name="join", description="Join your voice channel")
    async def join(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            await interaction.response.send_message("❌ You need to be in a voice channel!", ephemeral=True)
            return
        
        channel = interaction.user.voice.channel
        
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.move_to(channel)
        else:
            await channel.connect()
        
        await interaction.response.send_message(f"🔊 Joined **{channel.name}**!")


# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"🎵 {bot.user} is online!")
    print(f"📡 Connected to {len(bot.guilds)} server(s)")
    
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")
    
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening,
        name="vibinnnn'"
    ))


@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return
    
    voice_client = member.guild.voice_client
    if voice_client and before.channel == voice_client.channel:
        if len(voice_client.channel.members) == 1:
            await asyncio.sleep(30)
            # Recheck if still alone
            if voice_client and voice_client.channel and len(voice_client.channel.members) == 1:
                # Clean up player resources
                cog = bot.get_cog('MusicCog')
                if cog:
                    player = cog.players.get(member.guild.id)
                    if player:
                        player.queue.clear()
                        player.preloaded_sources.clear()
                        player.current = None
                await voice_client.disconnect()


async def main():
    async with bot:
        await bot.add_cog(MusicCog(bot))
        await bot.start(config.DISCORD_TOKEN)


if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        print("❌ Error: DISCORD_TOKEN not set in .env file!")
        exit(1)
    
    asyncio.run(main())
