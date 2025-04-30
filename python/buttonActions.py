import sys
import os
import configparser
import signal
import RPi.GPIO as GPIO

# some global stuff.
# initial status
current_state = 'context'
# initial Configuration file
dir = os.path.dirname(__file__)
config_file = os.path.join(dir, '..', 'config', 'eink_options.ini')
# Configuration for the matrix
config = configparser.ConfigParser()
config.read(config_file)
# Gpio pins for each button (from top to bottom)
BUTTONS = [5, 6, 16, 24]
# These correspond to buttons A, B, C and D respectively
LABELS = ['A', 'B', 'C', 'D']


def get_state(current_state: str) -> str:
    states = ['track', 'context', 'off']
    index = states.index(current_state)
    if index < (len(states)-1):
        return states[index+1]
    else:
        return states[0]


# "handle_button" will be called every time a button is pressed
# It receives one argument: the associated input pin.
def handle_button(pin):
    global current_state
    global config
    # do this every time to load the latest refresh token from the displayCoverArt.py->getSongInfo.py
    # use the shared client
    global sp
    label = LABELS[BUTTONS.index(pin)]

    try:
        if label == 'A':
            sp.next_track()
        elif label == 'B':
            sp.previous_track()
        elif label == 'C':
            playback = sp.currently_playing(additional_types='episode')
            if playback and playback.get('is_playing', False):
                sp.pause_playback()
            else:
                sp.start_playback()
        elif label == 'D':
            try:
                playback = sp.currently_playing(additional_types='episode')
            except Exception:
                playback = None
            if not playback or not playback.get('is_playing', False):
                open('/home/stavri/spotipi-eink/python/spotipi_cycle_idle','w').close()
        return

    except Exception as e:
        # fallback to cache-file auth with no prompts
        import spotipy
        import spotipy.util as util
        token = util.prompt_for_user_token(
            username=config['DEFAULT']['username'],
            scope='user-read-currently-playing,user-modify-playback-state',
            cache_path=config['DEFAULT']['token_file']
        )
        if token:
            sp = spotipy.Spotify(auth=token)
            # retry this same button action once
            handle_button(pin)
        # if token is still None, we silently give up


# CTR + C event clean up GPIO setup and exit nicly
def signal_handler(sig, frame):
    GPIO.cleanup()
    sys.exit(0)


def main():
    # Set up RPi.GPIO with the "BCM" numbering scheme
    # ── ONE-TIME Spotify setup ──
    from spotipy.oauth2 import SpotifyOAuth
    import spotipy
    import configparser

    global sp
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(os.path.dirname(__file__),
                          '..', 'config', 'eink_options.ini'))
    scope = 'user-read-currently-playing,user-modify-playback-state'
    auth = SpotifyOAuth(
        scope=scope,
        cache_path=cfg['DEFAULT']['token_file'],
        open_browser=False
    )
    sp = spotipy.Spotify(auth_manager=auth)

    # now your GPIO setup
    GPIO.setmode(GPIO.BCM)

    # Buttons connect to ground when pressed, so we should set them up
    # with a "PULL UP", which weakly pulls the input signal to 3.3V.
    GPIO.setup(BUTTONS, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    # Loop through out buttons and attach the "handle_button" function to each
    # We're watching the "FALLING" edge (transition from 3.3V to Ground) and
    # picking a generous bouncetime of 250ms to smooth out button presses.
    for pin in BUTTONS:
        GPIO.add_event_detect(pin, GPIO.FALLING, handle_button, bouncetime=250)

    # Finally, since button handlers don't require a "while True" loop,
    # We register the callback for CTRL+C handling
    signal.signal(signal.SIGINT, signal_handler)
    # We register the callback for SIGTERM handling
    signal.signal(signal.SIGTERM, signal_handler)
    # we pause the script to prevent it exiting immediately.
    signal.pause()


if __name__ == "__main__":
    main()
