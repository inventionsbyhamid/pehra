# Camera bridge

The bridge sends your society's existing cameras to the control room. It runs on any computer that is always on
at the society and on the same network as the NVR or DVR: a Windows PC, a Linux box or a Raspberry Pi.

It reads each camera from the recorder (RTSP, the substream), and sends a few pictures a second to the control room
over a secure connection. Nothing listens on the society's network, so you do not open or forward any port on the
router.

## What you need

* A computer at the society that stays on, with a wired connection if you can. One bridge handles many cameras.
  How many a small computer such as a Raspberry Pi can carry has not been measured yet, so add cameras a few at a
  time and check the messages.
* Windows, Linux, a Raspberry Pi with 64 bit Raspberry Pi OS, or any computer with Docker. On a Mac use Docker:
  OpenCV installed with pip on a Mac cannot read RTSP.
* The recorder's address on the network (for example `192.168.1.64`), a user and its password. A user that can
  only view is best.
* For each camera, a line from the control room. When an admin adds a camera and picks "Through the bridge", the
  control room shows a line like this, once:

  ```
  Ab3dEf6hIj9kLmNoPqRsTuVwXyZ01234=rtsp://admin:PASSWORD@192.168.1.64:554/Streaming/Channels/102
  ```

  The part before `=` is the camera's token. The part after it is the camera's address on your network.

Put all the lines in a file called `cameras.txt`, one camera per line (see `cameras.example.txt`). The file holds
the recorder's password, so keep it where only the bridge can read it.

## Run it with Docker (Linux, Raspberry Pi, Windows with Docker Desktop)

Build the image once, in this folder:

```
docker build -t camera-bridge .
```

Put the cameras file where the container can read it. Inside the container the bridge runs as user number 10001,
so give that user the file and nobody else:

```
sudo mkdir -p /etc/camera-bridge
sudo cp cameras.txt /etc/camera-bridge/cameras.txt
sudo chown 10001 /etc/camera-bridge/cameras.txt
sudo chmod 600 /etc/camera-bridge/cameras.txt
```

Start it, so it also starts again after a power cut:

```
docker run -d --name camera-bridge --restart unless-stopped \
  -v /etc/camera-bridge/cameras.txt:/cameras.txt:ro \
  camera-bridge --server https://YOUR-CONTROL-ROOM --cameras-file /cameras.txt
```

See what it is doing:

```
docker logs -f camera-bridge
```

## Run it without Docker

### Windows

1. Install Python 3.12 from python.org. Tick "Add python.exe to PATH".
2. Open Command Prompt in this folder and install what the bridge needs:

   ```
   py -m pip install opencv-python-headless==5.0.0.93 websockets==17.3
   ```

3. Start it:

   ```
   py pehra_bridge.py --server https://YOUR-CONTROL-ROOM --cameras-file cameras.txt
   ```

To start it when the computer starts, add a task in Task Scheduler: trigger "At startup", action "Start a program",
program `py`, arguments `pehra_bridge.py --server https://YOUR-CONTROL-ROOM --cameras-file cameras.txt --log-file
bridge.log`, and "Start in" set to this folder. Tick "Run whether user is logged on or not". A task has no window,
so its messages go to `bridge.log` in this folder (it keeps the last few megabytes). Keep `cameras.txt` readable
only by the account the task runs as: right click it, Properties, Security.

### Linux or Raspberry Pi (64 bit Raspberry Pi OS)

```
python3 -m venv ~/camera-bridge
~/camera-bridge/bin/pip install opencv-python-headless==5.0.0.93 websockets==17.3
~/camera-bridge/bin/python pehra_bridge.py --server https://YOUR-CONTROL-ROOM --cameras-file cameras.txt
```

Make the cameras file readable only by the user the bridge runs as (here `pi`):

```
chmod 600 /home/pi/bridge/cameras.txt
```

To start it with the computer, save this as `/etc/systemd/system/camera-bridge.service` (change the paths and the
user), then run `sudo systemctl enable --now camera-bridge`. Its messages are in `journalctl -u camera-bridge`.

```
[Unit]
Description=Camera bridge
After=network-online.target
Wants=network-online.target

[Service]
User=pi
ExecStart=/home/pi/camera-bridge/bin/python /home/pi/bridge/pehra_bridge.py --server https://YOUR-CONTROL-ROOM --cameras-file /home/pi/bridge/cameras.txt
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Other ways to name cameras

One camera, for a quick test:

```
python pehra_bridge.py --server https://YOUR-CONTROL-ROOM --token TOKEN --rtsp rtsp://admin:PASSWORD@192.168.1.64:554/Streaming/Channels/102
```

Several cameras on the command line, repeating `--camera`:

```
python pehra_bridge.py --server https://YOUR-CONTROL-ROOM --camera TOKEN1=rtsp://... --camera TOKEN2=rtsp://...
```

A file is better: anyone on the computer can see a command line, password included.

`--fps 2` sends fewer pictures a second than the control room asks for, if the society's upload is slow.

## Recorder addresses

The control room builds the address for you. For reference, the substream of camera (channel) 1:

| Recorder | Address |
|---|---|
| Hikvision | `rtsp://USER:PASSWORD@NVR:554/Streaming/Channels/102` (camera 2 is `202`, camera 17 is `1702`) |
| CP Plus, Dahua | `rtsp://USER:PASSWORD@NVR:554/cam/realmonitor?channel=1&subtype=1` |

Some CP Plus cameras on their own use port 5543. If a password has `@`, `:` or `/` in it, the control room writes it
encoded (`@` becomes `%40`); keep it that way.

On the recorder, set each camera's substream to H.264, 640x360 (or D1) at 6 to 8 frames a second. That is plenty
for the control room and light on the society's upload.

## What the messages mean

| Message | What to do |
|---|---|
| `connected, sending 5 frames a second` | All good. |
| `can't read the camera` | Check the recorder's address, user, password and channel. Open the same address in VLC on this computer to test it. |
| `can't reach the server` | Check this computer's internet. The bridge keeps trying, waiting a little longer each time. |
| `the server does not know this camera's token` or `this camera's link was replaced or removed` | The admin made a new link for this camera, or deleted it. Ask for the new line and put it in `cameras.txt`. |
| `another device is sending this camera` | A phone or another bridge took this camera over. Only one device sends each camera. |
| `the camera is turned off in the control room` | An admin turned it off. The bridge checks again every minute. |
| `every camera has stopped` | Every camera's link was replaced or removed. The bridge waits instead of trying the old links again. Put the new lines in `cameras.txt`, then restart it. |

The bridge never writes passwords to its messages: a camera appears as its recorder's address and port only, and a
token by its first four characters.
