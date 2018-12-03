#!/usr/bin/python
# -*- coding: utf-8 -*-

'''
    plugin.audio.spotify
    Spotify Player for Kodi
    main_service.py
    Background service which launches the spotty binary and monitors the player
'''

from utils import log_msg, ADDON_ID, log_exception, get_token, Spotty, PROXY_PORT, kill_spotty, parse_spotify_track
from player_monitor import ConnectPlayer
from connect_daemon import ConnectDaemon
from httpproxy import ProxyRunner
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs
import subprocess
import os
import sys
import xbmcvfs
import stat
import spotipy
import time
import threading
import thread
import StringIO


class MainService:
    '''our main background service running the various threads'''
    sp = None
    addon = None
    connect_player = None
    connect_daemon = None
    webservice = None
    spotty = None
    current_user = None
    auth_token = None

    def __init__(self):
        self.addon = xbmcaddon.Addon(id=ADDON_ID)
        self.win = xbmcgui.Window(10000)
        self.kodimonitor = xbmc.Monitor()
        self.spotty = Spotty()

        # spotipy and the webservice are always prestarted in the background
        # the auth key for spotipy will be set afterwards
        # the webserver is also used for the authentication callbacks from spotify api
        self.sp = spotipy.Spotify()
        self.connect_player = ConnectPlayer(sp=self.sp, spotty=self.spotty)

        self.proxy_runner = ProxyRunner(self.spotty)
        self.proxy_runner.start()
        webport = self.proxy_runner.get_port()
        log_msg('started webproxy at port {0}'.format(webport))

        # authenticate at startup
        self.renew_token()

        # start mainloop
        self.main_loop()

    def main_loop(self):
        '''main loop which keeps our threads alive and refreshes the token'''
        loop_timer = 5
        while not self.kodimonitor.waitForAbort(loop_timer):
            log_msg("main_loop step")
            # monitor logged in user
            cmd = self.win.getProperty("spotify-cmd").decode("utf-8")
            if cmd == "__LOGOUT__":
                log_msg("logout cmd received", xbmc.LOGNOTICE)
                self.stop_connect_daemon()
                self.win.clearProperty("spotify-cmd")
                self.current_user = None
                self.auth_token = None
                self.switch_user(True)
            elif not self.auth_token:
                # we do not yet have a token
                log_msg("Auth token is not set, renew token", xbmc.LOGNOTICE)
                if self.renew_token():
                    xbmc.executebuiltin("Container.Refresh")
            elif self.auth_token and self.auth_token['expires_at'] - 60 <= (int(time.time())):
                # token needs refreshing !
                log_msg("Auth token expires, renew token", xbmc.LOGNOTICE)
                self.renew_token()
            elif self.connect_player.connect_playing:
                # monitor fake connect OSD for remote track changes
                loop_timer = 2
                cur_playback = self.sp.current_playback()
                if cur_playback:
                    if cur_playback["is_playing"] and not xbmc.getCondVisibility("Player.Paused"):
                        player_title = xbmc.getInfoLabel("MusicPlayer.Title").decode("utf-8")
                        if player_title and player_title != cur_playback["item"]["name"]:
                            log_msg("Next track requested by Spotify Connect player")
                            trackdetails = cur_playback["item"]
                            self.connect_player.start_playback(trackdetails["id"])
                    elif cur_playback["is_playing"] and xbmc.getCondVisibility("Player.Paused"):
                        log_msg("playback resumed from pause")
                        self.connect_player.play()
                    elif not xbmc.getCondVisibility("Player.Paused"):
                        log_msg("Stop requested by Spotify Connect")
                        self.connect_player.pause()
                else:
                    self.connect_player.stop()
            else:
                loop_timer = 5

        # end of loop: we should exit
        self.close()

    def close(self):
        '''shutdown, perform cleanup'''
        log_msg('Shutdown requested !', xbmc.LOGNOTICE)
        kill_spotty()
        self.proxy_runner.stop()
        self.connect_player.close()
        self.connect_daemon.stop()
        del self.connect_player
        del self.addon
        del self.kodimonitor
        del self.win
        log_msg('stopped', xbmc.LOGNOTICE)


    def switch_user(self, restart_daemon=False):
        '''called whenever we switch to a different user/credentials'''
        log_msg("login credentials changed")
        if self.renew_token():
            xbmc.executebuiltin("Container.Refresh")

    def get_username(self):
        ''' get the current configured/setup username'''
        username = self.spotty.get_username()
        if not username:
            username = self.addon.getSetting("username").decode("utf-8")
            if not username and self.addon.getSetting("multi_account") == "true":
                username1 = self.addon.getSetting("username1").decode("utf-8")
                password1 = self.addon.getSetting("password1").decode("utf-8")
                if username1 and password1:
                    self.addon.setSetting("username", username1)
                    self.addon.setSetting("password", password1)
                    username = username1
        return username


    def stop_connect_daemon(self):
        ''' stop spotty connect daemon if needed '''
        if self.connect_daemon and self.connect_daemon.daemon_active:
            self.connect_daemon.stop()
            del self.connect_daemon

    def start_connect_daemon(self):
        '''start experimental spotify connect daemon'''
        if (not self.connect_daemon or not self.connect_daemon.daemon_active):
            if self.addon.getSetting("connect_player") == "true" and self.spotty.playback_supported:
                if not self.connect_daemon:
                    self.connect_daemon = ConnectDaemon(self.spotty)
                if not self.connect_daemon.daemon_active:
                    self.connect_daemon.start()


    def renew_token(self):
        '''refresh/retrieve the token'''
        result = False
        auth_token = None
        username = self.get_username()
        if username:
            # stop connect daemon
            self.stop_connect_daemon()
            # retrieve token
            log_msg("Retrieving auth token....")
            auth_token = get_token(self.spotty)
        if auth_token:
            log_msg("Retrieved auth token")
            self.auth_token = auth_token
            # only update token info in spotipy object
            self.sp._auth = auth_token["access_token"]
            me = self.sp.me()
            self.current_user = me["id"]
            log_msg("Logged in to Spotify - Username: %s" % self.current_user, xbmc.LOGNOTICE)
            # store authtoken and username as window prop for easy access by plugin entry
            self.win.setProperty("spotify-token", auth_token["access_token"])
            self.win.setProperty("spotify-username", self.current_user)
            self.win.setProperty("spotify-country", me["country"])
            result = True
        # start experimental spotify connect daemon
        self.start_connect_daemon()
        return result
