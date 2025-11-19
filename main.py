#!/usr/bin/env python3
# client_kivy.py
# Kiva Messenger - KivyMD client (patched, full)
# - Option 1 implemented: back arrow returns to Friends screen
# - Clean UI, friend list + requests, chat screen, login/register
# - Uses legacy tilde protocol compatible with your server
#
# Requirements:
#   pip install kivy kivymd
#
# Note: For testing on PC change HOST to your server IP if not local.

import socket
import threading
from functools import partial
from kivy.clock import Clock
from kivy.lang import Builder
from kivy.core.window import Window
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.utils import get_color_from_hex
from kivymd.app import MDApp
from kivymd.toast import toast
from kivymd.uix.list import OneLineListItem, TwoLineIconListItem, IconRightWidget
from kivymd.uix.button import MDIconButton
from kivymd.uix.dialog import MDDialog
from kivymd.uix.filemanager import MDFileManager
import base64
import io
from PIL import Image

# ---------------- CONFIG ----------------
HOST = "127.0.0.1"
PORT = 12345

client = None
current_user = None
current_dm = None
_last_login_attempt = None

# ---------------- KV ----------------
KV = r'''
ScreenManager:
    LoginScreen:
    FriendsScreen:
    ChatScreen:

<GradientBox@MDBoxLayout>:
    md_bg_color: app.theme_cls.primary_dark if root else 0,0,0,1

<LoginScreen>:
    name: "login"
    MDBoxLayout:
        orientation: "vertical"
        padding: dp(18), dp(24)
        spacing: dp(12)
        md_bg_color: 0.06,0.07,0.08,1

        MDLabel:
            text: "Kiva Messenger"
            font_style: "H4"
            halign: "center"
            size_hint_y: None
            height: self.texture_size[1]

        MDTextField:
            id: username
            hint_text: "Username"
            size_hint_y: None
            height: dp(48)
            helper_text_mode: "on_error"

        MDTextField:
            id: password
            hint_text: "Password"
            password: True
            size_hint_y: None
            height: dp(48)

        MDBoxLayout:
            size_hint_y: None
            height: dp(48)
            spacing: dp(12)
            MDRaisedButton:
                text: "Login"
                on_release: root.do_login()
            MDFlatButton:
                text: "Register"
                on_release: root.do_register()

        Widget:
            size_hint_y: None
            height: dp(12)

        MDSeparator:
            height: dp(1)

        MDBoxLayout:
            orientation: "horizontal"
            size_hint_y: None
            height: dp(48)
            spacing: dp(12)
            MDRaisedButton:
                text: "Skip to UI (Guest)"
                md_bg_color: app.theme_cls.primary_dark
                on_release: root.skip_guest()

<FriendsScreen>:
    name: "friends"
    MDBoxLayout:
        orientation: "vertical"
        md_bg_color: 0.07,0.08,0.09,1

        MDTopAppBar:
            title: "Friends"
            left_action_items: [["menu", lambda x: None]]
            right_action_items: [["account-plus", lambda x: app.open_add_friend_dialog()]]
            elevation: 4

        MDBoxLayout:
            orientation: "horizontal"
            padding: dp(12)
            spacing: dp(12)

            MDBoxLayout:
                orientation: "vertical"
                size_hint_x: 0.38

                MDLabel:
                    text: "Your Friends"
                    font_style: "Subtitle1"
                    size_hint_y: None
                    height: self.texture_size[1]

                ScrollView:
                    MDList:
                        id: friends_list

                MDSeparator:
                    height: dp(1)

                MDLabel:
                    text: "Pending Requests"
                    font_style: "Subtitle2"
                    size_hint_y: None
                    height: self.texture_size[1]

                ScrollView:
                    MDList:
                        id: requests_list

            MDBoxLayout:
                orientation: "vertical"
                padding: dp(6)
                MDLabel:
                    id: welcome_lbl
                    text: "Select a friend to start DM"
                    halign: "center"
                    size_hint_y: None
                    height: dp(36)

                MDCard:
                    orientation: "vertical"
                    size_hint: (1, 1)
                    padding: dp(12)
                    MDLabel:
                        text: "Right panel preview area"
                        halign: "center"

        Widget:
            size_hint_y: None
            height: dp(8)

<ChatScreen>:
    name: "chat"
    MDBoxLayout:
        orientation: "vertical"
        md_bg_color: 0.06,0.07,0.08,1

        MDTopAppBar:
            id: top_bar
            title: "Chat"
            left_action_items: [["arrow-left", lambda x: app.go_friends()]]
            elevation: 4

        ScrollView:
            MDList:
                id: chat_log
                padding: dp(12)

        MDBoxLayout:
            size_hint_y: None
            height: dp(64)
            padding: dp(10)
            spacing: dp(8)

            MDTextField:
                id: message_input
                hint_text: "Type a message..."
                mode: "rectangle"
                on_text_validate: root.send_message()
            MDIconButton:
                icon: "camera"
                on_release: root.open_image_picker()
'''
# ---------------- Helpers ----------------

def connect_to_server():
    global client
    if client:
        return True
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(6.0)
        client.connect((HOST, PORT))
        client.settimeout(None)
        threading.Thread(target=recv_loop, daemon=True).start()
        return True
    except Exception as e:
        print("connect failed:", e)
        client = None
        return False

def send(msg: str):
    global client
    try:
        if not client:
            if not connect_to_server():
                raise RuntimeError("no connection")
        client.sendall((msg + "\n").encode("utf-8"))
    except Exception as e:
        print("send failed:", e)
        toast("Send failed / disconnected")
        try:
            client.close()
        except:
            pass

def recv_loop():
    global client
    buf = ""
    try:
        while True:
            data = client.recv(4096)
            if not data:
                print("server closed")
                break
            try:
                chunk = data.decode("utf-8")
            except:
                chunk = data.decode(errors="ignore")
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                if line.strip():
                    Clock.schedule_once(lambda dt, l=line: handle_server(l.strip()))
    except Exception as e:
        print("recv error:", e)
    finally:
        try:
            client.close()
        except:
            pass

# ---------------- Server message handling ----------------
def handle_server(msg: str):
    global current_user, current_dm
    app = MDApp.get_running_app()
    sm = app.root
    # print("SERVER:", msg)

    if msg.startswith("SERVER~"):
        parts = msg.split("~")
        cmd = parts[1] if len(parts) > 1 else ""
        if cmd == "LOGIN_OK":
            # login OK: use _last_login_attempt (set before sending)
            global _last_login_attempt
            current_user = _last_login_attempt or None
            toast(f"Logged in as {current_user}")
            sm.current = "friends"
            return
        if cmd == "LOGIN_FAIL":
            toast("Login failed")
            return
        if cmd == "REGISTER_OK":
            toast("Registration OK — please login")
            return
        if cmd == "REGISTER_FAIL":
            toast("Registration failed")
            return
        # other server messages
        toast(f"Server: {cmd}")
        return

    if msg.startswith("FRIENDS~"):
        rest = msg.replace("FRIENDS~", "")
        friends = [x for x in rest.split(",") if x.strip()]
        screen = sm.get_screen("friends")
        screen.populate_friends(friends)
        return

    if msg.startswith("REQUESTS~"):
        rest = msg.replace("REQUESTS~", "")
        reqs = [x for x in rest.split(",") if x.strip()]
        screen = sm.get_screen("friends")
        screen.populate_requests(reqs)
        return

    if msg.startswith("DM~"):
        parts = msg.split("~", 2)
        if len(parts) >= 3:
            sender = parts[1]
            text = parts[2]
            # if chatting with sender, append; else notify
            if sender == current_dm and sm.current == "chat":
                chat = sm.get_screen("chat")
                chat.add_message(f"{sender}: {text}")
            else:
                toast(f"New DM from {sender}")
            return

    if msg.startswith("IMAGE~"):
        parts = msg.split("~", 2)
        if len(parts) >= 3:
            sender = parts[1]
            b64 = parts[2]
            if sender == current_dm and sm.current == "chat":
                chat = sm.get_screen("chat")
                chat.add_image(sender, b64)
            else:
                toast(f"Image from {sender}")
            return

    if msg.startswith("HISTORY_DM~"):
        # HISTORY_DM~sender~content~timestamp
        parts = msg.split("~", 3)
        if len(parts) >= 4:
            sender = parts[1]
            content = parts[2]
            sm.get_screen("chat").add_message(f"(history) {sender}: {content}")
            return

    # anything else
    print("Unhandled server message:", msg)

# ---------------- Screens ----------------
class LoginScreen(Screen):
    def do_login(self):
        global _last_login_attempt
        u = self.ids.username.text.strip()
        p = self.ids.password.text.strip()
        if not u or not p:
            toast("Fill username & password")
            return
        # ensure connection
        if not connect_to_server():
            toast("Unable to connect to server")
            return
        _last_login_attempt = u
        send(f"LOGIN~{u}~{p}")
        toast("Logging in...")

    def do_register(self):
        u = self.ids.username.text.strip()
        p = self.ids.password.text.strip()
        if not u or not p:
            toast("Fill username & password")
            return
        if not connect_to_server():
            toast("Unable to connect")
            return
        send(f"REGISTER~{u}~{p}")
        toast("Register sent")

    def skip_guest(self):
        global current_user
        current_user = "Guest"
        app = MDApp.get_running_app()
        app.root.current = "friends"
        toast("Continuing as Guest")

class FriendsScreen(Screen):
    def on_pre_enter(self):
        # request lists from server (server might push but safe to ask)
        if client:
            try:
                send("PING")
            except:
                pass

    def clear_lists(self):
        self.ids.friends_list.clear_widgets()
        self.ids.requests_list.clear_widgets()

    def populate_friends(self, friends):
        self.ids.friends_list.clear_widgets()
        if not friends:
            self.ids.friends_list.add_widget(OneLineListItem(text="(no friends)"))
            return
        for f in friends:
            item = OneLineListItem(text=f, on_release=partial(self.open_chat, f))
            # add context icon on the right
            icon = IconRightWidget(icon="account-details")
            item.add_widget(icon)
            self.ids.friends_list.add_widget(item)

    def populate_requests(self, requests):
        self.ids.requests_list.clear_widgets()
        if not requests:
            self.ids.requests_list.add_widget(OneLineListItem(text="(no requests)"))
            return
        for r in requests:
            # Two-line item with accept action
            item = TwoLineIconListItem(text=r, secondary_text="Tap to accept")
            icon = IconRightWidget(icon="account-check", on_release=partial(self.accept_request, r))
            item.add_widget(icon)
            self.ids.requests_list.add_widget(item)

    def open_chat(self, username, *args):
        global current_dm
        current_dm = username
        chat = self.manager.get_screen("chat")
        chat.ids.chat_log.clear_widgets()
        chat.ids.chat_title = f"Chat with {username}"
        # request history
        send(f"GET_HISTORY_DM~{username}~200")
        self.manager.current = "chat"

    def accept_request(self, username, *args):
        send(f"ACCEPT_FRIEND~{username}")
        toast(f"Accepted {username}")

class ChatScreen(Screen):
    def add_message(self, msg: str):
        self.ids.chat_log.add_widget(OneLineListItem(text=msg))

    def add_image(self, sender: str, b64: str):
        try:
            raw = base64.b64decode(b64)
            img = Image.open(io.BytesIO(raw))
            img.thumbnail((200, 200))
            from kivy.core.image import Image as CoreImage
            bio = io.BytesIO()
            img.save(bio, format="PNG")
            bio.seek(0)
            ci = CoreImage(bio, ext="png")
            # create a widget with the image
            from kivy.uix.image import Image as KivyImage
            w = KivyImage(texture=ci.texture, size_hint_y=None, height=200)
            self.ids.chat_log.add_widget(w)
            self.ids.chat_log.add_widget(OneLineListItem(text=f"[{sender} sent an image]"))
        except Exception as e:
            self.add_message(f"[{sender}] <image failed: {e}>")

    def send_message(self):
        global current_dm
        text = self.ids.message_input.text.strip()
        if not text:
            return
        if not current_dm:
            toast("Choose a friend first")
            return
        send(f"DM~{current_dm}~{text}")
        self.add_message(f"You: {text}")
        self.ids.message_input.text = ""

    def open_image_picker(self):
        # We will use a simple file dialog on desktop (kivy filechooser can be used).
        from tkinter import Tk, filedialog
        root = Tk()
        root.withdraw()
        path = filedialog.askopenfilename(title="Select image", filetypes=[("Images", "*.png;*.jpg;*.jpeg;*.gif;*.bmp")])
        try:
            root.destroy()
        except:
            pass
        if not path:
            return
        try:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            if not current_dm:
                toast("Choose friend first")
                return
            send(f"IMAGE~{current_dm}~{b64}")
            # show locally
            self.add_image("You", b64)
        except Exception as e:
            toast(f"Image error: {e}")

# ---------------- App ----------------
class KivaChatApp(MDApp):
    def build(self):
        # Optional window size for desktop test
        Window.size = (360, 640)
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "BlueGray"
        return Builder.load_string(KV)

    def go_friends(self):
        # used by top bar left arrow
        self.root.current = "friends"

    # helper for add friend dialog
    def open_add_friend_dialog(self):
        content = MDTextFieldRect = None
        from kivymd.uix.boxlayout import MDBoxLayout
        from kivymd.uix.textfield import MDTextField
        box = MDBoxLayout(orientation="vertical", spacing=10, size_hint_y=None, height="120dp")
        txt = MDTextField(hint_text="username", size_hint_x=1)
        box.add_widget(txt)
        def do_add(*args):
            target = txt.text.strip()
            if target:
                send(f"ADD_FRIEND~{target}")
                toast(f"Requested {target}")
            if dialog:
                dialog.dismiss()
        dialog = MDDialog(title="Add Friend", type="custom", content_cls=box, buttons=[])
        dialog.add_action_button("Add", action=lambda *a: do_add())
        dialog.add_action_button("Cancel", action=lambda *a: dialog.dismiss())
        dialog.open()

# ---------------- Entrypoint ----------------
def main():
    # try connect early (non-fatal)
    try:
        connect_to_server()
    except:
        pass
    KivaChatApp().run()

if __name__ == "__main__":
    main()
