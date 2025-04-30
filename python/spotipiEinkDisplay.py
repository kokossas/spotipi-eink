import time
import sys
import logging
from logging.handlers import RotatingFileHandler
import os
import traceback
import configparser
import requests
import signal
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageEnhance, ImageFilter
from typing import Optional


# recursion limiter for get song info to not go to infinity as decorator
def limit_recursion(limit):
    def inner(func):
        func.count = 0

        def wrapper(*args, **kwargs):
            func.count += 1
            if func.count < limit:
                result = func(*args, **kwargs)
            else:
                result = None
            func.count -= 1
            return result
        return wrapper
    return inner


class SpotipiEinkDisplay:
    def __init__(self, delay=1):
        signal.signal(signal.SIGTERM, self._handle_sigterm)
        self.delay = delay
        # Configuration for the matrix
        self.config = configparser.ConfigParser()
        self.config.read(os.path.join(os.path.dirname(__file__), '..', 'config', 'eink_options.ini'))
        from spotipy.oauth2 import SpotifyOAuth
        import spotipy

        scope = 'user-read-currently-playing,user-modify-playback-state'
        token_cache = self.config.get('DEFAULT', 'token_file')
        self.auth = SpotifyOAuth(scope=scope,
                                 cache_path=token_cache,
                                 open_browser=False)
        self.sp   = spotipy.Spotify(auth_manager=self.auth)
        # set spotipoy lib logger
        logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S', filename=self.config.get('DEFAULT', 'spotipy_log'), level=logging.INFO)
        logger = logging.getLogger('spotipy_logger')
        # automatically deletes logs more than 2000 bytes
        handler = RotatingFileHandler(self.config.get('DEFAULT', 'spotipy_log'), maxBytes=2000, backupCount=3)
        logger.addHandler(handler)
        # prep some vars before entering service loop
        self.song_prev = ''
        self.cycled_this_idle = False
        self.pic_counter = 0
        self.song_change_counter = 0 
        self.logger = self._init_logger()
        self.logger.info('Service instance created')
        if self.config.get('DEFAULT', 'model') == 'inky':
            from inky.auto import auto
            from inky.inky_uc8159 import CLEAN
            self.inky_auto = auto
            self.inky_clean = CLEAN
            self.logger.info('Loading Pimoroni inky lib')
        if self.config.get('DEFAULT', 'model') == 'waveshare4':
            from lib import epd4in01f
            self.wave4 = epd4in01f
            self.logger.info('Loading Waveshare 4" lib')

    def _init_logger(self):
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.DEBUG)
        stdout_handler = logging.StreamHandler()
        stdout_handler.setLevel(logging.DEBUG)
        stdout_handler.setFormatter(logging.Formatter('Spotipi eInk Display - %(message)s'))
        logger.addHandler(stdout_handler)
        return logger

    def _handle_sigterm(self, sig, frame):
        self.logger.warning('SIGTERM received stopping')
        sys.exit(0)

    def _break_fix(self, text: str, width: int, font: ImageFont, draw: ImageDraw):
        """
        Fix line breaks in text.
        """
        if not text:
            return
        if isinstance(text, str):
            text = text.split()  # this creates a list of words
        lo = 0
        hi = len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            t = ' '.join(text[:mid])  # this makes a string again
            w = int(draw.textlength(text=t, font=font))
            if w <= width:
                lo = mid
            else:
                hi = mid - 1
        t = ' '.join(text[:lo])  # this makes a string again
        w = int(draw.textlength(text=t, font=font))
        yield t, w
        yield from self._break_fix(text[lo:], width, font, draw)

    def _fit_text_top_down(self, img: Image, text: str, text_color: str, shadow_text_color: str, font: ImageFont, y_offset: int, font_size: int, x_start_offset: int = 0, x_end_offset: int = 0, offset_text_px_shadow: int = 0) -> int:
        """
        Fit text into container after applying line breaks. Returns the total
        height taken up by the text
        """
        width = img.width - x_start_offset - x_end_offset - offset_text_px_shadow
        draw = ImageDraw.Draw(img)
        pieces = list(self._break_fix(text, width, font, draw))
        y = y_offset
        h_taken_by_text = 0
        for t, _ in pieces:
            if offset_text_px_shadow > 0:
                draw.text((x_start_offset + offset_text_px_shadow, y + offset_text_px_shadow), t, font=font, fill=shadow_text_color)
            draw.text((x_start_offset, y), t, font=font, fill=text_color)
            new_height = font_size
            y += font_size
            h_taken_by_text += new_height
        return h_taken_by_text

    def _fit_text_bottom_up(self, img: Image, text: str, text_color: str, shadow_text_color: str, font: ImageFont, y_offset: int, font_size: int, x_start_offset: int = 0, x_end_offset: int = 0, offset_text_px_shadow: int = 0) -> int:
        """
        Fit text into container after applying line breaks. Returns the total
        height taken up by the text
        """
        width = img.width - x_start_offset - x_end_offset - offset_text_px_shadow
        draw = ImageDraw.Draw(img)
        pieces = list(self._break_fix(text, width, font, draw))
        y = y_offset
        if len(pieces) > 1:
            y -= (len(pieces) - 1) * font_size
        h_taken_by_text = 0
        for t, _ in pieces:
            if offset_text_px_shadow > 0:
                draw.text((x_start_offset + offset_text_px_shadow, y + offset_text_px_shadow), t, font=font, fill=shadow_text_color)
            draw.text((x_start_offset, y), t, font=font, fill=text_color)
            new_height = font_size
            y += font_size
            h_taken_by_text += new_height
        return h_taken_by_text

    def _display_clean(self):
        """cleans the display
        """
        try:
            if self.config.get('DEFAULT', 'model') == 'inky':
                inky = self.inky_auto()
                for _ in range(2):
                    for y in range(inky.height - 1):
                        for x in range(inky.width - 1):
                            inky.set_pixel(x, y, self.inky_clean)

                    inky.show()
                    time.sleep(1.0)
            if self.config.get('DEFAULT', 'model') == 'waveshare4':
                epd = self.wave4.EPD()
                epd.init()
                epd.Clear()
        except Exception as e:
            self.logger.error(f'Display clean error: {e}')
            self.logger.error(traceback.format_exc())

    def _convert_image_wave(self, img: Image, saturation: int = 2) -> Image:
        # blow out the saturation
        converter = ImageEnhance.Color(img)
        img = converter.enhance(saturation)
        # dither to 7-color palette
        palette_data = [0x00, 0x00, 0x00,
                        0xff, 0xff, 0xff,
                        0x00, 0xff, 0x00,
                        0x00, 0x00, 0xff,
                        0xff, 0x00, 0x00,
                        0xff, 0xff, 0x00,
                        0xff, 0x80, 0x00]
        # Image size doesn't matter since it's just the palette we're using
        palette_image = Image.new('P', (1, 1))
        # Set our 7 color palette (+ clear) and zero out the other 247 colors
        palette_image.putpalette(palette_data + [0, 0, 0] * 248)
        # Force source image and palette data to be loaded for `.im` to work
        img.load()
        palette_image.load()
        im = img.im.convert('P', True, palette_image.im)
        # create the new 7 color image and return it
        return img._new(im)

    def _display_image(self, image: Image, saturation: float = 0.5):
        """displays a image on the inky display

        Args:
            image (Image): Image to display
            saturation (float, optional): saturation. Defaults to 0.5.
        """
        try:
            if self.config.get('DEFAULT', 'model') == 'inky':
                inky = self.inky_auto()
                inky.set_image(image, saturation=saturation)
                inky.show()
            if self.config.get('DEFAULT', 'model') == 'waveshare4':
                epd = self.wave4.EPD()
                epd.init()
                epd.display(epd.getbuffer(self._convert_image_wave(image)))
                epd.sleep()
        except Exception as e:
            self.logger.error(f'Display image error: {e}')
            self.logger.error(traceback.format_exc())

    def _gen_pic(self, image: Optional[Image], artist: str, title: str, duration_ms: Optional[int], progress_ms: Optional[int], is_playing: bool) -> Image:
        import os
        from PIL import ImageDraw, ImageFont
        import textwrap
        try:
            from colorthief import ColorThief
            import io
        except ImportError:
            ColorThief = None

        target_w = self.config.getint('DEFAULT', 'width')
        target_h = self.config.getint('DEFAULT', 'height')

        PALETTE = {
            "black": (0, 0, 0), "white": (255, 255, 255), "green": (0, 255, 0),
            "blue": (0, 0, 255), "red": (255, 0, 0), "yellow": (255, 255, 0),
            "orange": (255, 128, 0)
        }

        def get_closest_color(rgb_tuple, palette):
            if not rgb_tuple: return palette["black"]
            min_dist = float('inf')
            closest_color_val = palette["black"]
            for color_rgb in palette.values():
                dist = sum([(a - b) ** 2 for a, b in zip(rgb_tuple, color_rgb)]) ** 0.5
                if dist < min_dist:
                    min_dist = dist
                    closest_color_val = color_rgb
            return closest_color_val

        bg_color = PALETTE["black"]
        if is_playing and image:
            bg_img = image.copy().convert("RGB")

            # ▶ preserve aspect ratio: zoom & center‑crop in one step
            bg_img = ImageOps.fit(
                bg_img,
                (target_w, target_h),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )

            # Apply blur
            bg_img = bg_img.filter(ImageFilter.GaussianBlur(radius=15))
            # Reduce brightness
            enhancer = ImageEnhance.Brightness(bg_img)
            bg_img = enhancer.enhance(0.8)  # 0.8 = 80% brightness
            img_new = bg_img
        else:
            if image:
                bg_img = image.copy().convert("RGB")

                # ▶ preserve aspect ratio: zoom & center‑crop in one step
                bg_img = ImageOps.fit(
                    bg_img,
                    (target_w, target_h),
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )

                # Blur
                bg_img = bg_img.filter(ImageFilter.GaussianBlur(radius=15))

                # Darken
                enhancer = ImageEnhance.Brightness(bg_img)
                img_new = enhancer.enhance(0.8)
            else:
                img_new = Image.new('RGB', (target_w, target_h), PALETTE["black"])

        draw = ImageDraw.Draw(img_new)

        art_size = int(min(target_w // 2, target_h - 100)*0.9)
        art_y = (target_h - art_size) // 2
        art_x = art_y

        if is_playing and image:
            album_art = image.resize((art_size, art_size), Image.Resampling.LANCZOS)
            corner_radius = 20
            mask = Image.new('L', (art_size, art_size), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.rounded_rectangle((0, 0, art_size, art_size), fill=255, radius=corner_radius)
            img_new.paste(album_art.convert("RGB"), (art_x, art_y), mask)
        elif image:
            image = image.convert("RGB")
            if image.height == 0:
                image = Image.new("RGB", (100, target_h), (0, 0, 0))  # fail-safe
            new_h = target_h
            new_w = int(image.width * (new_h / image.height))
            fitted = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
            img_new.paste(fitted, (0, 0))


        else:
            placeholder_color = PALETTE["white"] if bg_color == PALETTE["black"] else PALETTE["black"]
            draw.rounded_rectangle([(art_x, art_y), (art_x + art_size, art_y + art_size)], outline=placeholder_color, radius=20, width=2)

        try:
            font_dir = os.path.join(os.path.dirname(__file__), '..', 'resources')
            font_path_bold = self.config.get('DEFAULT', 'font_path_bold', fallback=os.path.join(font_dir, 'CircularStd-Bold.otf'))
            font_path_regular = self.config.get('DEFAULT', 'font_path_regular', fallback=font_path_bold)
            font_path_unicode = os.path.join(font_dir, 'NotoSans-Regular.ttf')
            font_size_title = self.config.getint('DEFAULT', 'font_size_title', fallback=24)
            font_size_artist = self.config.getint('DEFAULT', 'font_size_artist', fallback=18)

            font_title = ImageFont.truetype(font_path_unicode, font_size_title)
            #try:
            #    font_title = ImageFont.truetype(font_path_bold, font_size_title)
            #except:
            #    font_title = ImageFont.truetype(font_path_unicode, font_size_title)

            font_artist = ImageFont.truetype(font_path_unicode, font_size_artist)
            #try:
            #    font_artist = ImageFont.truetype(font_path_regular, font_size_artist)
            #except:
            #    font_artist = ImageFont.truetype(font_path_unicode, font_size_artist)

        except Exception as e:
            self.logger.error(f"Font loading error: {e}, using default.")
            font_title = ImageFont.load_default()
            font_artist = ImageFont.load_default()

        text_color = PALETTE["white"] if bg_color == PALETTE["black"] else PALETTE["black"]

        if is_playing:
            text_start_x = art_x + art_size + 20
            text_width_limit = target_w - text_start_x - 15

            # Fonts
            label_font_size = 12
            artist_font_size = 14

            font_label = ImageFont.truetype(font_path_unicode, label_font_size)
            #try:
            #    font_label = ImageFont.truetype(font_path_bold, label_font_size)
            #except:
            #    font_label = ImageFont.truetype(font_path_unicode, label_font_size)

            font_artist_small = ImageFont.truetype(font_path_unicode, artist_font_size)
            #try:
            #    font_artist_small = ImageFont.truetype(font_path_regular, artist_font_size)
            #except:
            #    font_artist_small = ImageFont.truetype(font_path_unicode, artist_font_size)


            # Prepare wrapped lines
            lines_label = ["SONG"]
            lines_title = textwrap.wrap(title, width=max(1, text_width_limit // (font_size_title // 2)))
            lines_artist = textwrap.wrap(artist, width=max(1, text_width_limit // (artist_font_size // 2)))

            # Estimate total height
            spacing = 5
            total_height = (
                label_font_size +
                len(lines_title) * (font_size_title + spacing) +
                len(lines_artist) * (artist_font_size + spacing) +
                spacing * 3
            )

            # Center vertically
            current_y = art_y + (art_size - total_height) // 2

            # Draw label
            draw.text((text_start_x, current_y), "SONG", font=font_label, fill=text_color)
            current_y += label_font_size + spacing

            # Title
            for line in lines_title:
                draw.text((text_start_x, current_y), line, font=font_title, fill=text_color)
                current_y += font_size_title + spacing

            current_y += spacing

            # Artist
            for line in lines_artist:
                draw.text((text_start_x, current_y), line, font=font_artist_small, fill=text_color)
                current_y += artist_font_size + spacing
            # Draw Spotify logo bottom-right
            try:
                logo_path = os.path.expanduser('~/spotipi-eink/images/spotify_logo.png')
                logo_img = Image.open(logo_path).convert("RGBA")
                logo_height = 24
                logo_ratio = logo_img.width / logo_img.height
                logo_resized = logo_img.resize((int(logo_height * logo_ratio), logo_height), Image.Resampling.LANCZOS)

                logo_x = target_w - logo_resized.width - 20
                logo_y = target_h - logo_height - 20
                img_new.paste(logo_resized, (logo_x, logo_y), logo_resized)
            except Exception as e:
                self.logger.warning(f"Could not draw Spotify logo: {e}")


        else:
            # Idle prompt
            idle_text = "No song playing"
            label_text = "LISTEN ON"
            text_area_x = art_x + art_size + 20
            text_area_width = target_w - text_area_x - 15


            idle_font = font_title
            label_font = ImageFont.truetype(font_path_unicode, 16)
            #try:
            #    label_font = ImageFont.truetype(font_path_bold, 16)
            #except:
            #    label_font = ImageFont.truetype(font_path_unicode, 16)


            # Measure text
            idle_bbox = draw.textbbox((0, 0), idle_text, font=idle_font)
            label_bbox = draw.textbbox((0, 0), label_text, font=label_font)

            # Load and resize logo
            logo_path = os.path.expanduser('~/spotipi-eink/images/spotify_logo.png')
            logo_img = Image.open(logo_path).convert("RGBA")
            logo_height = font_size_title
            logo_ratio = logo_img.width / logo_img.height
            logo_resized = logo_img.resize((int(logo_height * logo_ratio), logo_height), Image.Resampling.LANCZOS)

            # Positioning
            total_block_height = idle_bbox[3] - idle_bbox[1] + 24 + logo_height
            start_y = target_h // 2 - total_block_height // 2
            center_x = text_area_x + text_area_width // 2

            # Draw "No song playing"
            idle_text_w = idle_bbox[2] - idle_bbox[0]
            draw.text((center_x - idle_text_w // 2, start_y), idle_text, font=idle_font, fill=text_color)

            # Draw "LISTEN ON" + logo below
            label_w = label_bbox[2] - label_bbox[0]
            logo_w = logo_resized.width
            padding = 10
            combo_w = label_w + padding + logo_w

            label_x = center_x - combo_w // 2
            label_y = start_y + (idle_bbox[3] - idle_bbox[1]) + 24

            draw.text((label_x, label_y + (logo_height - (label_bbox[3] - label_bbox[1])) // 2), label_text, font=label_font, fill=text_color)
            img_new.paste(logo_resized, (label_x + label_w + padding, label_y), logo_resized)

        return img_new


    def _display_update_process(self, song_request: list):
        """Display update process that jude by the song_request list if a song is playing and we need to download the album cover or not

        Args:
            song_request (list): song_request list
            config (configparser.ConfigParser): config object
            pic_counter (int): picture refresh counter

        Returns:
            int: updated picture refresh counter
        """
        if song_request:
            # download cover
            try:
                resp = requests.get(song_request[1], stream=True)
                resp.raise_for_status()
                cover = Image.open(resp.raw).convert("RGB")
            except Exception as e:
                self.logger.error(f"Error downloading cover: {e}")
                cover = None

            image = self._gen_pic(
                cover,
                artist=song_request[2],
                title=song_request[0],
                duration_ms=None,
                progress_ms=None,
                is_playing=True
            )
        else:
            # not song playing use logo
            import random

            idle_dir = os.path.expanduser('~/spotipi-eink/images/idle')
            idle_files = [f for f in os.listdir(idle_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
            idle_img = None
            if idle_files:
                selected = random.choice(idle_files)
                try:
                    idle_img = Image.open(os.path.join(idle_dir, selected)).convert("RGB")
                except:
                    idle_img = None


            image = self._gen_pic(
                idle_img,
                artist="",
                title="",
                duration_ms=None,
                progress_ms=None,
                is_playing=False
            )

        # clean screen every x pics
        if self.pic_counter > self.config.getint('DEFAULT', 'display_refresh_counter'):
            self._display_clean()
            self.pic_counter = 0
        # display picture on display
        self._display_image(image)
        image.save("test_output.png")
        self.pic_counter += 1

    @limit_recursion(limit=10)
    def _get_song_info(self) -> list:
        """get the current played song from Spotify's Web API"""
        try:
            # try the cached client first
            result = self.sp.currently_playing(additional_types='episode')
        except Exception as e:
            self.logger.warning(f"Cached Spotify client failed ({e}), falling back to cache_file auth")
            # fallback to old util.prompt_for_user_token (uses cache_file)
            from spotipy.util import prompt_for_user_token
            token = prompt_for_user_token(
                username=self.config.get('DEFAULT','username'),
                scope='user-read-currently-playing,user-modify-playback-state',
                cache_path=self.config.get('DEFAULT','token_file')
            )
            if not token:
                self.logger.error("Fallback auth failed, skipping song info")
                return []
            import spotipy
            self.sp = spotipy.Spotify(auth=token)
            # retry once
            try:
                result = self.sp.currently_playing(additional_types='episode')
            except Exception as e2:
                self.logger.error(f"Retry after fallback also failed ({e2})")
                return []

        if not result:
            return []

    def start(self):
        self.logger.info('Service started')
        # clean screen initially
        self._display_clean()
        try:
            while True:
                try:
                    song_request = self._get_song_info()
                    flag = '/home/stavri/spotipi-eink/python/spotipi_cycle_idle'
                    if not song_request and os.path.exists(flag):
                        os.remove(flag)
                        # only cycle once per idle session
                        if not self.cycled_this_idle:
                            self._display_update_process(song_request=[])
                            self.song_prev = 'NO_SONG'
                            self.cycled_this_idle = True
                        continue
                    if song_request:
                        if self.song_prev != song_request[0] + song_request[1]:
                            self.cycled_this_idle = False
                            self.song_prev = song_request[0] + song_request[1]
                            self._display_update_process(song_request=song_request)
                    #CONSTANT UPDATES FOR TESTING
                    #self._display_update_process(song_request=song_request if song_request else [])
                    #self.song_prev = song_request[0] + song_request[1] if song_request else 'NO_SONG'

                    if not song_request:
                        if self.song_prev != 'NO_SONG':
                            # set fake song name to update only once if no song is playing.
                            self.song_prev = 'NO_SONG'
                            self._display_update_process(song_request=song_request)
                except Exception as e:
                    self.logger.error(f'Error: {e}')
                    self.logger.error(traceback.format_exc())
                time.sleep(self.delay)
        except KeyboardInterrupt:
            self.logger.info('Service stopping')
            sys.exit(0)


if __name__ == "__main__":
    service = SpotipiEinkDisplay()
    service.start()
