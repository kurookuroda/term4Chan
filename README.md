```
apt-get update -qq && apt-get install -y -qq libaa1
```

```
python3 cli.py
```

### usage
listboards, lb:
    list available boards.
        ex: lb

listthreads, lt:
    list the threads in a board. lt <board name>
        ex: lt b

getreplies, gr:
    list replies to a thread. gr <board name> <threadID>
        ex: gr a 115931903

webhook, wh:
    send a thread to the Discord webhooks in config.webhook_file.
    Images are sent as URLs. Numbers start at 1 (1 = the first post)
    and a range includes both ends.
        wh                  the thread you are viewing (all posts)
        wh 1                only post 1
        wh 3-10             posts 3 to 10
        wh 5-               post 5 to the last
        wh <board> <threadID> [range]    a thread given explicitly
    Each message ends with the thread URL (a link to that post).
    Options: --no-images (no image URLs), --no-url (no thread URL),
             --dry-run (print only)
        ex: wh a 115931903 3-10 --dry-run

info, i:
    show the current board / thread.

disableimages, di / enableimages, ei:
    turn the thumbnail display off / on.

help, h:
    shows this message

exit, quit, q:
    exits the program

While viewing threads or replies (the [/board/ threadID ...] prompt):
    Enter or n  next          o  open the thread (thread list only)
    wh [range]  send          h  help           i  current thread
    q           back'''


```
**Note:** This project is not actively maintained. Last tested on 3.8.10

4Chan BBS
=========

About
-----

4Chan BBS is a Python project which allows users to browse the popular image board 
4Chan through a telnet connection and command line interface.

Why?
----

Do you remember the good old days of networked computing before GUI browsers existed?

I don't, I'm too young.

Regardless, I think it's time for old-school telnet BBS server to make a comeback. So 
with that in mind, I made a BBS server for the popular image board 4Chan! The server 
back-end communicates through 4Chan's API to provide real boards, threads, and posts 
in real time and presents this data to the user through a command line interface. No 
more pesky GUIs!

4Chan BBS even supports images by converting them into a more CLI friendly ASCII string format!

Installation
------------

The easiest way to setup 4Chan BBS is to install Python 3.8, PIP and libaa1. Install `libaa1`
with a command similar to `sudo apt install libaa1`.  Then run `pip install -r requirements.txt` 
to get all the required libraries.

To start the server run `python Server.py`.

If you get an error while trying to install Pillow, you may have to download some 
additonal libraries using a command like `sudo apt-get build-dep python-imaging`.

Configuration
-------------

`config.py` includes all the configuration settings for the server.

License
-------

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

See <http://www.gnu.org/licenses/> for the full license.
```
