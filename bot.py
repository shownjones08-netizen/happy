#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════╗
║   SHOWN x OTP  ·  PREMIUM TELEGRAM ACCOUNT-SELLING STORE  (ULTRA)      ║
║   Rich formatting · Inline buttons · Premium emoji · Force-join          ║
║   Telethon account login · UPI wallet (QR 15-min) · OTP relay            ║
║   Auto login-detection · Instant delivery · Full admin panel             ║
╚══════════════════════════════════════════════════════════════════════════╝
Run:  python3 account_store_bot.py
"""

import os, sys, re, io, json, time, html, asyncio, sqlite3, threading, random, string, logging, traceback, socket
from datetime import datetime, timedelta

# ── core deps ──────────────────────────────────────────────────────────────
import telebot
from telebot import types
import requests

# ── optional deps ──────────────────────────────────────────────────────────
try:
    from telethon import TelegramClient, events, functions
    from telethon.sessions import StringSession
    from telethon.errors import (SessionPasswordNeededError, PhoneCodeInvalidError,
                                 PhoneCodeExpiredError, PhoneNumberInvalidError,
                                 AuthRestartError)
    TELETHON_OK = True
except Exception as _e:  # pragma: no cover
    TELETHON_OK = False
    print("[warn] Telethon not installed -> account login disabled:", _e)

try:
    import qrcode
    QR_OK = True
except Exception:
    QR_OK = False

try:
    from flask import Flask, request as flask_request, jsonify
    FLASK_OK = True
except Exception:
    FLASK_OK = False

# ══════════════════════════════════════════════════════════════════════════
#  PHONE VPS BRIDGE  (embedded — single-file build)
#  --------------------------------------------------------------------------
#  Merged from phone_bridge.py.  Drives a real Android phone's OFFICIAL
#  Telegram / WhatsApp apps over ADB so SMS/OTP codes arrive like on a real
#  handset (NOT web, NOT QR).  Optional: if no phone is connected the bot
#  keeps working normally.
# ══════════════════════════════════════════════════════════════════════════
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
phone_bridge.py  —  "Phone VPS" controller for the QURESHI x OTP store bot
==========================================================================

WHY THIS EXISTS
---------------
Telegram and WhatsApp only deliver an SMS login code when you use their
*official* mobile apps.  A server-side library (Telethon / Baileys) is a
"linked device", so it can never trigger a fresh SMS — that is a hard rule set
by Telegram & WhatsApp themselves, not a bug in the bot.

This module turns a real Android phone (or a cloud Android instance such as
Genymotion Cloud, AWS Device Farm, Redroid, a spare handset, …) into a
controllable "phone VPS": the bot drives the *official* Telegram and WhatsApp
apps over ADB (Android Debug Bridge) and reads the SMS / in-app code that the
apps receive.  That is exactly the "proper app login, SMS comes directly"
behaviour that was requested.

It is 100% pure Python — no Node, no browser — and it is optional: if no phone
is connected the rest of the bot keeps working normally.

QUICK START
-----------
1) Enable Developer Options + USB debugging on the Android phone.
2) Plug it into the server (USB)  OR  connect over Wi-Fi:
       adb connect 192.168.1.50:5555
3) Test (single-file build — no separate module needed):
       python3 account_store_bot.py phone-test            # device + app status
       python3 account_store_bot.py phone-test 192.168.1.50:5555
       python3 account_store_bot.py phone-otp org.telegram.messenger

USED BY THE BOT
---------------
    pb = PhoneBridge()          # class is embedded in this same file
    if pb.ok():
        code = pb.read_otp("org.telegram.messenger")
"""

import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

# ── well-known package names ────────────────────────────────────────────────
PKG_TELEGRAM = "org.telegram.messenger"          # Telegram (official)
PKG_TELEGRAM_X = "org.thunderdog.challegram"     # Telegram X
PKG_WHATSAPP = "com.whatsapp"                    # WhatsApp (official)
PKG_WHATSAPP_BIZ = "com.whatsapp.w4b"            # WhatsApp Business
PKG_SMS = "com.android.mms"

# common Android keycodes
KEY_BACK = 4
KEY_HOME = 3
KEY_ENTER = 66
KEY_DELETE = 67
KEY_TAB = 61

_DEFAULT_ADB = os.environ.get("ADB_BIN", "adb")


class PhoneBridgeError(Exception):
    pass


class PhoneBridge:
    """Thin, dependency-free wrapper around `adb` for UI automation.

    Every method is defensive: if adb or a device is missing it returns
    False / None instead of raising, so the bot never crashes because a phone
    was unplugged.
    """

    def __init__(self, serial=None, adb_bin=None, connect=None):
        self.adb = adb_bin or _DEFAULT_ADB
        self.serial = serial or os.environ.get("PHONE_SERIAL") or None
        self._tmp = "/sdcard/_pb_dump.xml"
        if connect:                       # e.g. "192.168.1.50:5555"
            self.connect(connect)

    # ── low level ───────────────────────────────────────────────────────────
    def adb_available(self):
        return bool(shutil.which(self.adb)) or os.path.exists(self.adb)

    def _base(self):
        cmd = [self.adb]
        if self.serial:
            cmd += ["-s", self.serial]
        return cmd

    def _sh(self, *args, timeout=25):
        """Run an adb command, return (returncode, stdout+stderr)."""
        try:
            p = subprocess.run(self._base() + list(args),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=timeout)
            return p.returncode, p.stdout.decode("utf-8", "replace")
        except Exception as e:
            return 1, str(e)

    def _sh_out(self, *args, timeout=25):
        return self._sh(*args, timeout=timeout)[1]

    # ── device management ───────────────────────────────────────────────────
    def devices(self):
        """Return a list of {serial, state, model} for every attached device."""
        out = self._sh_out("devices", "-l")
        found = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line or line.startswith("*"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            d = {"serial": parts[0], "state": parts[1]}
            m = re.search(r"model:(\S+)", line)
            if m:
                d["model"] = m.group(1).replace("_", " ")
            found.append(d)
        return found

    def online_devices(self):
        return [d for d in self.devices() if d["state"] == "device"]

    def connect(self, host_port):
        """`adb connect host:port` for a Wi-Fi / cloud phone."""
        rc, out = self._sh("connect", host_port, timeout=20)
        time.sleep(1.0)
        return "connected" in out.lower() or "already" in out.lower()

    def disconnect(self, host_port=None):
        if host_port:
            self._sh("disconnect", host_port)
        else:
            self._sh("disconnect")

    def pick_device(self):
        """Auto-select the first online device if none was set explicitly."""
        if self.serial:
            return self.serial
        devs = self.online_devices()
        if devs:
            self.serial = devs[0]["serial"]
            return self.serial
        return None

    def ok(self):
        """True when adb exists AND at least one device is usable."""
        return self.adb_available() and bool(self.pick_device())

    def model(self):
        if not self.ok():
            return None
        return self._sh_out("shell", "getprop", "ro.product.model").strip()

    def android_version(self):
        if not self.ok():
            return None
        return self._sh_out("shell", "getprop", "ro.build.version.release").strip()

    def has_package(self, package):
        if not self.ok():
            return False
        out = self._sh_out("shell", "pm", "list", "packages", package)
        return ("package:" + package) in out

    # ── UI automation ───────────────────────────────────────────────────────
    def dump_ui(self):
        """Return the current window hierarchy as an XML string (or '')."""
        if not self.ok():
            return ""
        self._sh("shell", "uiautomator", "dump", self._tmp, timeout=20)
        xml = self._sh_out("exec-out", "cat", self._tmp, timeout=20)
        if "<hierarchy" not in xml:
            # some ROMs need a second try
            time.sleep(0.6)
            self._sh("shell", "uiautomator", "dump", self._tmp, timeout=20)
            xml = self._sh_out("exec-out", "cat", self._tmp, timeout=20)
        return xml

    @staticmethod
    def _center(bounds):
        m = re.findall(r"\[(\d+),(\d+)\]", bounds or "")
        if len(m) != 2:
            return None
        (x1, y1), (x2, y2) = (int(m[0][0]), int(m[0][1])), (int(m[1][0]), int(m[1][1]))
        return (x1 + x2) // 2, (y1 + y2) // 2

    def nodes(self):
        """Parse the UI dump into a list of dicts with text/bounds/center."""
        xml = self.dump_ui()
        if not xml:
            return []
        try:
            root = ET.fromstring(xml)
        except Exception:
            return []
        out = []
        for n in root.iter("node"):
            a = n.attrib
            c = self._center(a.get("bounds", ""))
            out.append({
                "text": a.get("text", ""),
                "rid": a.get("resource-id", ""),
                "desc": a.get("content-desc", ""),
                "cls": a.get("class", ""),
                "clickable": a.get("clickable") == "true",
                "bounds": a.get("bounds", ""),
                "center": c,
            })
        return out

    def find(self, text=None, rid=None, desc=None, contains=True):
        """Return matching nodes. `text`/`desc` match case-insensitively."""
        res = []
        for n in self.nodes():
            if text is not None:
                hay = (n["text"] or "").lower()
                needle = text.lower()
                if (needle in hay) if contains else (hay == needle):
                    res.append(n); continue
            if rid is not None and rid in (n["rid"] or ""):
                res.append(n); continue
            if desc is not None:
                hay = (n["desc"] or "").lower()
                needle = desc.lower()
                if (needle in hay) if contains else (hay == needle):
                    res.append(n)
        return res

    def tap(self, x, y):
        self._sh("shell", "input", "tap", str(int(x)), str(int(y)))
        time.sleep(0.4)

    def tap_text(self, text, contains=True, desc=None, rid=None, timeout=12):
        """Wait up to `timeout`s for a node and tap it. Returns True on tap."""
        end = time.time() + timeout
        while time.time() < end:
            cands = self.find(text=text, rid=rid, desc=desc, contains=contains)
            for n in cands:
                if n["center"]:
                    self.tap(*n["center"])
                    return True
            time.sleep(0.8)
        return False

    def tap_node(self, node):
        if node and node.get("center"):
            self.tap(*node["center"])
            return True
        return False

    def type_text(self, s):
        """Type into the focused field (spaces escaped as %s for adb)."""
        safe = str(s).replace(" ", "%s")
        self._sh("shell", "input", "text", safe)
        time.sleep(0.3)

    def key(self, code):
        self._sh("shell", "input", "keyevent", str(code))
        time.sleep(0.3)

    def launch(self, package):
        """Start an app by package (uses monkey, which finds the launcher)."""
        self._sh("shell", "monkey", "-p", package,
                 "-c", "android.intent.category.LAUNCHER", "1")
        time.sleep(2.0)

    def force_stop(self, package):
        self._sh("shell", "am", "force-stop", package)

    def screenshot(self, local_path):
        """Save a PNG screenshot of the device to `local_path`."""
        try:
            with open(local_path, "wb") as f:
                p = subprocess.run(self._base() + ["exec-out", "screencap", "-p"],
                                   stdout=f, stderr=subprocess.DEVNULL, timeout=25)
            return os.path.exists(local_path) and os.path.getsize(local_path) > 0
        except Exception:
            return False

    def screen_on(self):
        """Wake the screen and dismiss a simple keyguard if possible."""
        self._sh("shell", "input", "keyevent", "224")   # WAKEUP
        self.key(82)                                    # MENU (unlock-ish)
        self._sh("shell", "input", "swipe", "540", "1600", "540", "400", "120")

    # ── OTP reading ─────────────────────────────────────────────────────────
    _CODE_RE = re.compile(r"(?<!\d)(\d{3,4}[-\s]?\d{3})(?!\d)|(?<!\d)(\d{4,6})(?!\d)")

    def read_otp(self, package=None, retries=6, delay=3):
        """Best-effort: return the newest numeric code shown on the phone.

        Looks at (1) the on-screen UI text, (2) the notification shade
        (`dumpsys notification`).  `package` narrows the search to one app.
        """
        for _ in range(max(1, retries)):
            code = self._otp_from_ui(package) or self._otp_from_notifications(package)
            if code:
                return code
            time.sleep(delay)
        return None

    def _otp_from_ui(self, package=None):
        for n in self.nodes():
            t = (n.get("text") or "") + " " + (n.get("desc") or "")
            m = self._CODE_RE.search(t)
            if m:
                return (m.group(1) or m.group(2)).replace("-", "").replace(" ", "")
        return None

    def _otp_from_notifications(self, package=None):
        if not self.ok():
            return None
        out = self._sh_out("shell", "dumpsys", "notification", "--noredact", timeout=25)
        blocks = re.split(r"\n\s*NotificationRecord", out)
        for b in reversed(blocks):
            if package and package not in b:
                # also accept the app label
                continue
            for kw in ("code", "otp", "verification", "login", "verify"):
                if kw in b.lower():
                    m = self._CODE_RE.search(b)
                    if m:
                        return (m.group(1) or m.group(2)).replace("-", "").replace(" ", "")
        # last resort: any code in the newest notification
        m = self._CODE_RE.search(out[-4000:])
        return (m.group(1) or m.group(2)).replace("-", "").replace(" ", "") if m else None

    def read_sms_otp(self, retries=6, delay=3):
        """Read the newest OTP from the SMS inbox (needs the shell SMS access)."""
        for _ in range(max(1, retries)):
            out = self._sh_out("shell", "content", "query",
                               "--uri", "content://sms/inbox",
                               "--projection", "body", "--sort",
                               "date DESC LIMIT 5", timeout=20)
            m = self._CODE_RE.search(out)
            if m:
                return (m.group(1) or m.group(2)).replace("-", "").replace(" ", "")
            time.sleep(delay)
        return None

    # ── Telegram official-app login ─────────────────────────────────────────
    def login_telegram(self, phone, timeout=180):
        """Drive the official Telegram app to log in with `phone`.

        Returns {"ok":bool, "phone":.., "otp":.., "note":..}.  The app receives
        the SMS/in-app code itself (a real phone), so this is the genuine
        'app login' — not a web/QR session.
        """
        phone = str(phone).strip()
        if not self.ok():
            return {"ok": False, "note": "no android device connected"}
        pkg = PKG_TELEGRAM if self.has_package(PKG_TELEGRAM) else (
            PKG_TELEGRAM_X if self.has_package(PKG_TELEGRAM_X) else None)
        if not pkg:
            return {"ok": False, "note": "Telegram app not installed on the phone"}
        self.screen_on()
        self.force_stop(pkg)
        self.launch(pkg)
        time.sleep(3)
        # onboarding ("Start Messaging")
        self.tap_text("Start Messaging", timeout=8)
        # country + phone entry
        self.tap_text("Country", timeout=5)
        self.tap_text("Phone number", timeout=8)
        # type the full international number
        self.type_text(phone)
        self.tap_text("Next", timeout=6) or self.key(KEY_ENTER)
        # the app now shows "We've sent the code to the Telegram app / SMS"
        otp = self.read_otp(pkg, retries=int(timeout // 6), delay=6)
        return {"ok": bool(otp), "phone": phone, "otp": otp,
                "note": "code read from the official Telegram app" if otp
                        else "no code seen yet — open the app to finish"}

    # ── WhatsApp official-app login ─────────────────────────────────────────
    def login_whatsapp(self, phone, timeout=240):
        """Drive the official WhatsApp app to register `phone` (SMS OTP).

        WhatsApp has NO 'phone login' for a 2nd device — a real phone always
        *registers* with an SMS code.  This drives that exact flow, so the SMS
        arrives on the phone and is read automatically.
        """
        phone = str(phone).strip().lstrip("+")
        if not self.ok():
            return {"ok": False, "note": "no android device connected"}
        pkg = PKG_WHATSAPP if self.has_package(PKG_WHATSAPP) else (
            PKG_WHATSAPP_BIZ if self.has_package(PKG_WHATSAPP_BIZ) else None)
        if not pkg:
            return {"ok": False, "note": "WhatsApp app not installed on the phone"}
        self.screen_on()
        self.force_stop(pkg)
        self.launch(pkg)
        time.sleep(3)
        self.tap_text("Agree and continue", timeout=8)
        # country picker is optional; most builds keep the last one
        self.tap_text("Country", timeout=4)
        self.tap_text("Phone number", timeout=8)
        self.type_text(phone)
        self.tap_text("Next", timeout=6) or self.key(KEY_ENTER)
        # WhatsApp shows a "Verify" dialog; tap it to trigger the SMS
        self.tap_text("Verify", timeout=8)
        otp = self.read_otp(pkg, retries=int(timeout // 6), delay=6) or \
            self.read_sms_otp(retries=int(timeout // 8), delay=6)
        return {"ok": bool(otp), "phone": "+" + phone, "otp": otp,
                "note": "SMS code read from the phone" if otp
                        else "no SMS yet — check the phone"}


# ── tiny CLI (handy for testing on the server) ──────────────────────────────

PHONE_BRIDGE_OK = True



# ═══════════════════════════════════════════════════════════════════════════
#  CONFIG  —  edit these
# ═══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
#  CREDENTIALS — BLANKED OUT
#  Fill these in yourself (or set the matching environment variables).
#  Nothing is hard-coded any more.
# ══════════════════════════════════════════════════════════════════════════════
BOT_TOKEN   = os.environ.get("8946021148:AAESyYIYWOd0BFHJ_0zUXtLlU7uvRaQWlFE", "").strip()          # <-- apna bot token yahan
# built-in Telegram API creds used to log accounts in (admin only sends the phone)
DEFAULT_API_ID   = int(os.environ.get("TG_API_ID", "0") or 0)  # <-- apna API ID yahan
DEFAULT_API_HASH = os.environ.get("TG_API_HASH", "").strip()   # <-- apna API Hash yahan
OWNER_ID    = int(os.environ.get("OWNER_ID", "8256108006") or 0)        # <-- apna owner ID yahan
ADMIN_IDS   = {OWNER_ID}

STORE_NAME  = "SHOWN"
STORE_TAG   = "𝗧𝗚 𝗦𝗧𝗢𝗥𝗘"
SERVER_NAME = "Server 1"
# Two servers: Server 1 = cheapest, no guarantee. Server 2 = premium, guaranteed.
SERVER_LABELS = {1: "Server 1", 2: "Server 2"}
SERVER_EMOJI  = {1: "\U0001F7E2", 2: "\U0001F535"}
SERVER_NOTE   = {1: "Cheapest accounts \u2022 No guarantee",
                 2: "Premium quality \u2022 Guaranteed"}
SUPPORT_URL = "https://t.me/WhoIsShown"
CHANNEL_URL = "https://t.me/shownXher
# ── TGShark API (Server 1 = auto-fetched API accounts) ──────────────────────
# Docs: https://tgsharkapi.store/docs/
TGSHARK_API_KEY = os.environ.get("TGSHARK_API_KEY", "").strip()   # <-- apni TGShark API key
TGSHARK_BASE    = os.environ.get("TGSHARK_BASE", "").strip().rstrip("/")  # <-- apna base URL


def terms_text():
    """Long, premium Terms of Service (built with premium emoji at call time)."""
    return (
        f"{pe('lock')} <b>STORE TERMS OF SERVICE, POLICIES &amp; USER AGREEMENT</b> {pe('shield')}\n"
        f"{pe('warn')} <i>Please read every section carefully before purchasing.</i>\n"
        f"{'━'*24}\n\n"
        f"{pe('star')} <b>1. Acceptance of Terms</b>\n"
    "By purchasing, browsing or using any service of QURESHI x OTP TG STORE you agree to "
    "these Terms &amp; Policies in full. If you do not agree, please stop using the store "
    "immediately.\n\n"
    "<b>2. Nature of Products</b>\n"
    "We sell pre-registered Telegram accounts / session files for personal and legitimate "
    "business use only. All accounts are sold <b>as-is</b>. We do not guarantee account age, "
    "profile completeness, username availability or the presence of premium features unless "
    "explicitly stated on the product label.\n\n"
    "<b>3. Delivery &amp; Login</b>\n"
    "Delivery is automated and instant. After purchase you receive the phone number and login "
    "steps. A login code (OTP) is delivered to you through this bot. If the account has 2FA "
    "enabled, our team will provide the password on request during the delivery window.\n\n"
    "<b>4. No Refund Policy</b>\n"
    "Once an account has been delivered and the login code has been shared, the sale is final. "
    "No refunds, no exchanges and no chargebacks are permitted. Wallet balance is "
    "non-refundable and non-transferable.\n\n"
    "<b>5. Acceptable Use</b>\n"
    "You may NOT use purchased accounts for spam, scams, fraud, harassment, impersonation, "
    "illegal activities or any purpose that violates Telegram's Terms of Service or the laws "
    "of your country. Accounts used for such activity will be terminated without refund.\n\n"
    "<b>6. Account Bans &amp; Restrictions</b>\n"
    "Telegram may restrict or ban accounts at any time for reasons beyond our control. We are "
    "not responsible for bans, restrictions or limitations that occur after a successful "
    "login. We are also not liable for any loss of data, contacts or chats.\n\n"
    "<b>7. Wallet &amp; Payments</b>\n"
    "Deposits are verified manually or automatically. Fraudulent payment proofs, fake UTR "
    "numbers or chargeback attempts will result in a permanent ban and forfeiture of balance. "
    "Minimum deposit and UPI details are shown at checkout.\n\n"
    "<b>8. Referral Program</b>\n"
    "Referral rewards are credited only after the invited user has joined our official "
    "force-join channel(s). Self-referrals, fake accounts and bot-generated referrals are "
    "strictly prohibited and will be reversed with a ban.\n\n"
    "<b>9. Developer API</b>\n"
    "Resellers may use the REST API with their personal key. Every API purchase is deducted "
    "from your own wallet balance. You are responsible for keeping your API key secret; all "
    "activity under your key is your responsibility.\n\n"
    "<b>10. Privacy</b>\n"
    "We store only the minimum data required to operate the store (your Telegram ID, balance "
    "and order history). We never sell your data to third parties.\n\n"
    "<b>11. Changes to Terms</b>\n"
    "We may update these Terms at any time. Continued use of the store after changes means you "
    "accept the new Terms.\n\n"
    "<b>12. Limitation of Liability</b>\n"
    "Our total liability for any claim is limited to the amount you paid for the specific "
    "order in question. By using this store you waive any right to class-action or collective "
    "claims.\n\n"
    f"{pe('cross')} <b>13. Prohibited Activities</b>\n"
    "You agree NOT to: resell accounts as verified/aged when they are not, use our store "
    "to launder money, run chargeback fraud, abuse the referral program with self-invites or "
    "fake accounts, scrape our API to attack the server, or impersonate our staff. Any breach "
    "results in an immediate permanent ban, forfeiture of all wallet balance and possible "
    "reporting to the relevant authorities.\n\n"
    f"{pe('gift')} <b>14. Referral Program &amp; Anti-Fraud</b>\n"
    "Referral rewards are credited <b>only after</b> the invited user joins our official "
    "force-join channel(s). Self-referrals, bot-generated referrals, duplicate accounts and "
    "any attempt to game the system are strictly forbidden. We reserve the right to reverse "
    "any reward and ban any account involved in referral fraud, with or without notice.\n\n"
    f"{pe('card')} <b>15. Payments, UPI &amp; QR</b>\n"
    "All deposits are made via the UPI ID / QR shown at checkout. The UPI details are set by "
    "the store owner and may change at any time. Always pay to the UPI shown inside this bot "
    "at the moment of deposit. We are not responsible for payments sent to any other UPI ID. "
    "Fraudulent UTR numbers, edited screenshots or fake payment proofs lead to a permanent ban.\n\n"
    f"{pe('wallet')} <b>16. Wallet Balance</b>\n"
    "Wallet balance is a store credit, not real currency. It is non-withdrawable, "
    "non-refundable and non-transferable, and can only be used to purchase products inside "
    "this store. Unused balance never expires but has no cash value.\n\n"
    f"{pe('clock')} <b>17. Delivery Window &amp; OTP</b>\n"
    "Delivery is automated and usually instant. If the login code (OTP) is delayed, you must "
    "keep this chat open and wait. If a purchased account fails to deliver a code within the "
    "delivery window, contact support with your order ID. Replacement is offered at our sole "
    "discretion and only when the fault is on our side.\n\n"
    f"{pe('laptop')} <b>18. Developer / Reseller API</b>\n"
    "The REST API is provided as-is for resellers. Every API purchase is deducted from your "
    "own wallet balance. You must keep your secret key private; all activity performed with "
    "your key is your responsibility. We may rate-limit, suspend or revoke API access at any "
    "time to protect the platform.\n\n"
    f"{pe('shield')} <b>19. Account Security After Delivery</b>\n"
    "Once an account is delivered and you confirm a successful login, security of that account "
    "is entirely your responsibility. Change the password, enable 2FA and secure the account "
    "immediately. We are not liable for accounts lost due to your own negligence.\n\n"
    f"{pe('gear')} <b>20. Service Availability</b>\n"
    "We aim for 24/7 uptime but do not guarantee uninterrupted service. Maintenance, Telegram "
    "outages, hosting issues or force-join verification failures may temporarily interrupt the "
    "store. No compensation is provided for downtime.\n\n"
    f"{pe('chart')} <b>21. Governing Law &amp; Disputes</b>\n"
    "These Terms are governed by the laws applicable at the store owner's location. Any "
    "dispute must first be raised with our support team. By using the store you agree to "
    "resolve disputes individually and waive any right to class-action or collective claims.\n\n"
    f"{pe('refresh')} <b>22. Changes to These Terms</b>\n"
    "We may add, remove or change any part of these Terms at any time without prior notice. "
    "The latest version is always shown in this bot. Continued use of the store after an update "
    "means you fully accept the revised Terms.\n\n"
    f"{pe('check')} <b>23. Your Acknowledgement</b>\n"
    "By tapping <b>Accept Terms &amp; Policies</b> you confirm that you have read, understood "
    "and agreed to every section above, that you are legally able to enter this agreement, and "
    "that you accept all risks associated with buying pre-registered accounts.\n\n"
    f"{pe('sod')}\n"
    "<i>Last updated: 2026 \u00b7 QURESHI x OTP TG STORE</i>")

CURRENCY    = "₹"
BOT_USERNAME = os.environ.get("BOT_USERNAME", "").strip()
PORT        = int(os.environ.get("PORT", 5001))
BASE_DIR    = os.path.abspath(os.path.dirname(__file__))

def _resolve_db_path():
    """DB location. Priority: DB_PATH env -> next to this file (if writable)
    -> system temp dir. Lets the bot run even on a read-only filesystem."""
    env = os.environ.get("DB_PATH", "").strip()
    if env:
        return env
    p = os.path.join(BASE_DIR, "store.db")
    try:
        os.makedirs(BASE_DIR, exist_ok=True)
        with open(p, "ab"):
            pass
        return p
    except Exception:
        import tempfile
        return os.path.join(tempfile.gettempdir(), "store.db")

DB_PATH     = _resolve_db_path()
EMOJI_FILE  = os.path.join(BASE_DIR, "emoji_config.json")
FLAGS_FILE  = os.path.join(BASE_DIR, "flags_config.json")
FLAGS_PACK_FILE = os.path.join(BASE_DIR, "flags_emoji_pack.json")

# ═══════════════════════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("store")

# ═══════════════════════════════════════════════════════════════════════════
#  PREMIUM EMOJI REGISTRY  (char, custom_emoji_id)
#  Edit live from the admin panel, or directly in emoji_config.json
# ═══════════════════════════════════════════════════════════════════════════
USE_PREMIUM = True
# ── VERIFIED premium-emoji IDs (validated live via Telethon) ──
DEFAULT_EMOJI = {
    "star":        ('⭐', "6129909635613726974"),
    "crown":       ('👑', "5805337324967432449"),
    "gem":         ('💎', "5796253585100509494"),
    "fire":        ('🔥', "5999246912174166759"),
    "rocket":      ('🚀', "6129639980387015660"),
    "check":       ('✅', "5807455714506969247"),
    "cross":       ('❌', "5796291784539639311"),
    "money":       ('💰', "5776103539872896061"),
    "cart":        ('🛒', "5775925350269719113"),
    "bolt":        ('⚡', "6129805465476929485"),
    "gift":        ('🎁', "6001538474795078519"),
    "box":         ('📦', "5244978916157827574"),
    "shield":      ('🛡', "4958900559139570572"),
    "card":        ('💳', "5776026402260259662"),
    "phone":       ('📱', "5323379047315555501"),
    "lock":        ('🔒', "6129906126625447892"),
    "clock":       ('⏰', "5902050947567194830"),
    "globe":       ('🌐', "5775903750879187931"),
    "wave":        ('👋', "4958832114540741368"),
    "eyes":        ('👀', "5805401092346875873"),
    "link":        ('🔗', "4958689671950369798"),
    "user":        ('👤', "5902335789798265487"),
    "chart":       ('📊', "6001546944470587024"),
    "wallet":      ('👛', "6039641775377748623"),
    "sod":         ('✨', "4958489311726011319"),
    "arrow":       ('➡️', "4956282853882069908"),
    "bell":        ('🔔', "4956290155326473271"),
    "search":      ('🔍', "4958587679361991667"),
    "plus":        ('➕', "4956507094124594921"),
    "minus":       ('➖', "5447155993286832278"),
    "warn":        ('⚠️', "4956611513369494230"),
    "info":        ('ℹ️', "5776121926627888870"),
    "doc":         ('📄', "5447421246172069841"),
    "memo":        ('📝', "5444889156792646660"),
    "send":        ('📤', "5776366177123045379"),
    "megaphone":   ('📣', "4958686613933655185"),
    "gear":        ('⚙️', "5893161718179173515"),
    "robot":       ('🤖', "6129873536413605540"),
    "trophy":      ('🏆', "6156436440260549720"),
    "medal":       ('🏅', "5444931419270839381"),
    "key":         ('🔑', "5445373775132522312"),
    "refresh":     ('🔄', "4956371914323920049"),
    "back":        ('⬅️', "5447506720316225765"),
    "down":        ('⬇️', "5796664978542956370"),
    "up":          ('⬆️', "5444988177263654261"),
    "party":       ('🎉', "4956308456182121758"),
    "heart":       ('❤️', "5776064528684945996"),
    "thumbsup":    ('👍', "5805232832708087309"),
    "clap":        ('👏', "5805205039974715415"),
    "eyes2":       ('👀', "5805401092346875873"),
    "folder":      ('📁', "5447389837076231920"),
    "chart2":      ('📈', "4956599758044005301"),
    "moon":        ('🌙', "4958941455818163141"),
    "sun":         ('☀️', "5893080732275840699"),
    "rainbow":     ('🌈', "4958957716564345977"),
    "flower":      ('🌹', "5999164083729863129"),
    "apple":       ('🍏', "6129741453284350735"),
    "cake":        ('🍰', "4958931508673905639"),
    "coffee":      ('☕', "4956252540002894879"),
    "plane":       ('✈️', "5893333516871012690"),
    "car":         ('🚗', "4958801766301828295"),
    "pin":         ('📌', "4956232383721374836"),
    "location":    ('📍', "4958728373900674046"),
    "camera":      ('📸', "5798586602745761373"),
    "video":       ('🎥', "6264778055454036969"),
    "mail":        ('✉️', "6129432481927010933"),
    "chat":        ('💬', "5805567170142277810"),
    "question":    ('❓', "5776056162088652694"),
    "excl":        ('❗', "4956368164817470478"),
    "no":          ('🚫', "4956337889593000947"),
    "star2":       ('🌟', "4956745198521549627"),
    "sparkle":     ('✨', "4958489311726011319"),
    "target":      ('🎯', "5350460637182993292"),
    "bank":        ('🏦', "5264895611517300926"),
    "point":       ('👉', "5471978009449731768"),
    "tag":         ('🏷', "5854776233950188167"),
    "clip":        ('📎', "5877495434124988415"),
    "pencil":      ('✏️', "5879841310902324730"),
    "pizza":       ('🍕', "5370980663778351052"),
    "home":        ('🏠', "5967822972931542886"),
    "building":    ('🏢', "5264733042710181045"),
    "book":        ('📖', "5897850551156084824"),
    "laptop":      ('💻', "5877318502947229960"),
    "tv":          ('📺', "5836749569014109509"),
    "ticket":      ('🎫', "5418010521309815154"),
    "bag":         ('🛍', "4956496816267854718"),
    "mobile_arrow": ('📲', "5445059250382469069"),
    "money_wing":  ('💸', "6044329586152445777"),
    "inbox":       ('📥', "5776303079758500750"),
    "pencil2":     ('📝', "5444889156792646660"),
    "id2":         ('🪪', "5936017305585586269"),
    "key2":        ('🗝', "5330100898767054648"),
    "chart3":      ('📉', "4956552088201986883"),
    "dollar":      ('💵', "5996747477431096516"),
    "rupee":       ('🇮🇳', "6129712921816604452"),
    "check2":      ('✔️', "4956721670690702265"),
    "cross2":      ('✖️', "5226660202035554522"),
    "glow":        ('💫', "4956259055468282692"),
    "boom":        ('💥', "6129532314146838421"),
    "ring":        ('💍', "4958564417819116436"),
    "medal2":      ('🥇', "6265004494719816749"),
    "folder2":     ('📂', "5447282724886839705"),
    "file":        ('📄', "5447421246172069841"),
    "note":        ('🗒', "5877597667231534929"),
    "calendar":    ('📅', "5967412305338568701"),
    "clock2":      ('🕐', "5805429164253123352"),
    "hourglass":   ('⌛', "6267229004311303657"),
    "lock2":       ('🔐', "6129731845442510016"),
    "unlock":      ('🔓', "6034962180875490251"),
    "wrench":      ('🔧', "5258023599419171861"),
    "hammer":      ('🔨', "5875450995332353523"),
    "tool":        ('🛠', "5988023995125993550"),
    "belloff":     ('🔕', "5909123362839335003"),
    "horn":        ('📢', "5771695636411847302"),
    "chain":       ('⛓', "6001078118725456537"),
    "world":       ('🌍', "5399898266265475100"),
    "users":       ('👥', "5085015333119460184"),
    "admin":       ('🧑\u200d💻', "5190458330719461749"),
    "telephone":   ('☎️', "5967591100532134862"),
    "call":        ('📞', "5893297890117292323"),
    "satellite":   ('📡', "5447448489149625830"),
    "thought":     ('💭', "6129939837823753679"),
    "eye":         ('👁', "4958808208752772190"),
    "search2":     ('🔎', "5893382531037794941"),
    "price":       ('💲', "5899776109548934640"),
    "bookmark":    ('🔖', "4958621433509970793"),
    "id":          ('🔖', "4958621433509970793"),
    "shield2":     ('🛡', "4958900559139570572"),
    "mail2":       ('✉️', "6129432481927010933"),
    "person":      ('👤', "5902335789798265487"),
    "diamond2":    ('💎', "5796253585100509494"),
    "label":       ('🏷', "5854776233950188167"),
}
PREMIUM_EMOJI = dict(DEFAULT_EMOJI)

# country flag premium emoji:  "IN": "custom_emoji_id"  (fallback = unicode flag)
FLAGS = {}
# custom-emoji pack (FlagsEmoji2024):  unicode-flag -> custom_emoji_id
FLAGS_PACK = {}

# ── FlagsEmoji2024 custom-emoji pack (embedded so no extra file is needed) ──
EMBEDDED_FLAGS_PACK = {
    '🇦🇩': '5294215205763434181',
    '🇦🇪': '5294314831824835370',
    '🇦🇫': '5291937511591925566',
    '🇦🇬': '5294005972136647964',
    '🇦🇮': '5292186323342350940',
    '🇦🇱': '5294202819077756005',
    '🇦🇲': '5291978717508164018',
    '🇦🇴': '5294516785482062829',
    '🇦🇷': '5292208210495689627',
    '🇦🇸': '5291994273879709721',
    '🇦🇹': '5291975174160145850',
    '🇦🇺': '5294444247779399477',
    '🇦🇼': '5294007002928798927',
    '🇦🇽': '5294077418917616055',
    '🇦🇿': '5294323533428579078',
    '🇧🇧': '5294526187165471742',
    '🇧🇩': '5291824687096027834',
    '🇧🇪': '5291774466043435275',
    '🇧🇫': '5294153164960848949',
    '🇧🇬': '5294308947719640437',
    '🇧🇭': '5294108398516720753',
    '🇧🇮': '5294051631933967760',
    '🇧🇯': '5293984969746566866',
    '🇧🇳': '5292098293692650297',
    '🇧🇴': '5294201479047957700',
    '🇧🇷': '5291892229751723900',
    '🇧🇸': '5294031587321600012',
    '🇧🇹': '5294121983498277263',
    '🇧🇼': '5294026179957772585',
    '🇧🇾': '5294134426018536120',
    '🇧🇿': '5294171848068584842',
    '🇨🇦': '5292290347450259214',
    '🇨🇫': '5294210571493724819',
    '🇨🇬': '5294035229453865597',
    '🇨🇭': '5291791748991835084',
    '🇨🇮': '5293991322003200135',
    '🇨🇰': '5292098684534675100',
    '🇨🇱': '5294231037012888049',
    '🇨🇲': '5291997306126626950',
    '🇨🇳': '5294068833277990704',
    '🇨🇴': '5294010206974397371',
    '🇨🇷': '5292063805105263554',
    '🇨🇺': '5291963947115631526',
    '🇨🇻': '5292203503211535593',
    '🇨🇾': '5294062721539526918',
    '🇨🇿': '5294242852467923382',
    '🇩🇪': '5292013274815028523',
    '🇩🇯': '5294127214768468283',
    '🇩🇰': '5294531860817268837',
    '🇩🇲': '5294485513825178032',
    '🇩🇴': '5294522197140857947',
    '🇩🇿': '5294048127240655242',
    '🇪🇨': '5292083733753517221',
    '🇪🇪': '5291951143818123103',
    '🇪🇬': '5293992082212409502',
    '🇪🇷': '5291922054004625949',
    '🇪🇸': '5294513087515216901',
    '🇪🇹': '5292245976143124155',
    '🇪🇺': '5291992809295861098',
    '🇫🇮': '5294049961191690629',
    '🇫🇲': '5291838156113470124',
    '🇫🇷': '5291817660529533837',
    '🇬🇦': '5294321325815389139',
    '🇬🇧': '5293993521026453119',
    '🇬🇪': '5294349389131697267',
    '🇬🇭': '5294347396266873249',
    '🇬🇮': '5292055799286224027',
    '🇬🇱': '5292014752283774878',
    '🇬🇲': '5294399820637688352',
    '🇬🇳': '5291892096607739008',
    '🇬🇶': '5292170045416297012',
    '🇬🇷': '5291948395039054764',
    '🇬🇹': '5294336633078831209',
    '🇬🇼': '5294409819321550432',
    '🇬🇾': '5292062692708736193',
    '🇭🇰': '5292166459118606932',
    '🇭🇳': '5291901034434682297',
    '🇭🇷': '5291999676948569127',
    '🇭🇹': '5292045130587462814',
    '🇭🇺': '5294229581018975260',
    '🇮🇪': '5294471971793293647',
    '🇮🇱': '5294069056616289553',
    '🇮🇲': '5294318478252070646',
    '🇮🇳': '5291933173674957761',
    '🇮🇶': '5294325010897327367',
    '🇮🇷': '5294220170745630736',
    '🇮🇸': '5294354358408859664',
    '🇮🇹': '5291826830284709120',
    '🇯🇪': '5291950280529697493',
    '🇯🇲': '5294505107465982830',
    '🇯🇴': '5291988613112814801',
    '🇯🇵': '5291799063321139445',
    '🇰🇪': '5292111852904416801',
    '🇰🇬': '5292091954320922577',
    '🇰🇭': '5294225191562400452',
    '🇰🇮': '5294538934628405146',
    '🇰🇲': '5294351381996521508',
    '🇰🇵': '5294193812531333564',
    '🇰🇷': '5294408281723262763',
    '🇰🇼': '5292066437920218075',
    '🇰🇿': '5294227175837290463',
    '🇱🇦': '5291981530711746037',
    '🇱🇧': '5294193108156699621',
    '🇱🇮': '5292048742654957785',
    '🇱🇰': '5292102670264328257',
    '🇱🇷': '5291793810576137439',
    '🇱🇸': '5292040693886247604',
    '🇱🇹': '5294343084119708700',
    '🇱🇺': '5294423709245787718',
    '🇱🇻': '5292236016113966127',
    '🇱🇾': '5291858711826946840',
    '🇲🇦': '5292108962391414885',
    '🇲🇨': '5294378161117614233',
    '🇲🇩': '5294158486425325375',
    '🇲🇬': '5291991568050312348',
    '🇲🇭': '5294180730060954484',
    '🇲🇰': '5294023611567332075',
    '🇲🇱': '5292086972158858331',
    '🇲🇲': '5294254478944393569',
    '🇲🇳': '5294316532631883496',
    '🇲🇷': '5294429743674840973',
    '🇲🇹': '5294532213004588353',
    '🇲🇺': '5294127824653797277',
    '🇲🇻': '5292004203844097218',
    '🇲🇼': '5294241881805312589',
    '🇲🇽': '5294535073452809778',
    '🇲🇾': '5291858351049696702',
    '🇲🇿': '5294086708931874940',
    '🇳🇦': '5292021761670404922',
    '🇳🇪': '5291809418487290691',
    '🇳🇬': '5294456308047563965',
    '🇳🇮': '5294240825243358100',
    '🇳🇱': '5291917797692042265',
    '🇳🇴': '5291761718580502030',
    '🇳🇵': '5294458756178924088',
    '🇳🇷': '5294463274484521342',
    '🇳🇺': '5294471336138134209',
    '🇳🇿': '5294189019347833274',
    '🇴🇲': '5291813666209946812',
    '🇵🇦': '5291959935616178405',
    '🇵🇪': '5292099427564018941',
    '🇵🇬': '5291917995260533077',
    '🇵🇭': '5291798075478661634',
    '🇵🇰': '5291825606219029010',
    '🇵🇱': '5292190970496963836',
    '🇵🇷': '5292121516580820347',
    '🇵🇸': '5294289826525238172',
    '🇵🇹': '5294436555492973610',
    '🇵🇾': '5294525611639852679',
    '🇶🇦': '5292166360334357676',
    '🇷🇴': '5294107724206856227',
    '🇷🇸': '5294458584380230360',
    '🇷🇺': '5294335323113807278',
    '🇷🇼': '5294191265615729158',
    '🇸🇦': '5294163983983463099',
    '🇸🇧': '5294283890880433237',
    '🇸🇨': '5291891186074672309',
    '🇸🇩': '5294177148058228060',
    '🇸🇪': '5291737091238026321',
    '🇸🇬': '5294451304410663668',
    '🇸🇮': '5294279359689938006',
    '🇸🇰': '5294538440707166931',
    '🇸🇱': '5294494314213167952',
    '🇸🇲': '5292147350809106831',
    '🇸🇳': '5292087023698466689',
    '🇸🇴': '5294058817414255960',
    '🇸🇷': '5294396668131692138',
    '🇸🇹': '5292183188016222701',
    '🇸🇻': '5294337307388695687',
    '🇸🇾': '5294013428199869487',
    '🇸🇿': '5294312482477724867',
    '🇹🇨': '5294320866253884749',
    '🇹🇩': '5291780728105753403',
    '🇹🇬': '5294097669688415562',
    '🇹🇭': '5293994384314882755',
    '🇹🇯': '5294120269806328883',
    '🇹🇲': '5294098958178603764',
    '🇹🇳': '5294484680601521871',
    '🇹🇴': '5294283689016973348',
    '🇹🇷': '5293993400767367408',
    '🇹🇹': '5294362935458548705',
    '🇹🇼': '5294095745543069603',
    '🇹🇿': '5292146096678658977',
    '🇺🇦': '5294263837678131580',
    '🇺🇬': '5294192317882716626',
    '🇺🇸': '5294244076533600593',
    '🇺🇾': '5291928449210932974',
    '🇺🇿': '5294217645304864345',
    '🇻🇪': '5294476442854247878',
    '🇻🇮': '5294228039125718124',
    '🇻🇳': '5294235963340379688',
    '🇻🇺': '5294448585696368047',
    '🇾🇪': '5294058972033076492',
    '🇿🇦': '5294325281480266304',
    '🇿🇲': '5294100109229838880',
    '🇿🇼': '5294422158762592930',
    '🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f': '5294410107084365278',
    '🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f': '5294434665707368018',
    '🏴\U000e0067\U000e0062\U000e0077\U000e006c\U000e0073\U000e007f': '5294139949346476093',
    '🚩': '5294236848103643477',
}

# sensible premium-flag defaults so common countries render premium out of the box
DEFAULT_FLAGS = {
    "IN": "6129712921816604452",   # Indian flag (verified premium emoji)
}

def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning("save_json %s failed: %s", path, e)

def load_configs():
    """Load emoji + flag config from disk (create defaults if missing)."""
    global PREMIUM_EMOJI, FLAGS, FLAGS_PACK
    em = _load_json(EMOJI_FILE, None)
    if not em:
        em = {k: list(v) for k, v in DEFAULT_EMOJI.items()}
        _save_json(EMOJI_FILE, em)
    for k, v in em.items():
        if isinstance(v, (list, tuple)) and len(v) == 2:
            PREMIUM_EMOJI[k] = (v[0], str(v[1]))
    fl = _load_json(FLAGS_FILE, None)
    if fl is None:
        fl = {}
        _save_json(FLAGS_FILE, fl)
    FLAGS = {str(k).upper(): str(v) for k, v in fl.items()}
    # apply premium-flag defaults (admin captures override these)
    for _cc, _eid in DEFAULT_FLAGS.items():
        FLAGS.setdefault(_cc, _eid)
    # load the FlagsEmoji2024 custom-emoji pack (unicode flag -> custom_emoji_id)
    _pack = _load_json(FLAGS_PACK_FILE, {}) or EMBEDDED_FLAGS_PACK
    FLAGS_PACK = {str(k): str(v) for k, v in _pack.items()}

def save_emoji(key, char, eid):
    PREMIUM_EMOJI[key] = (char, str(eid))
    data = {k: [v[0], v[1]] for k, v in PREMIUM_EMOJI.items()}
    _save_json(EMOJI_FILE, data)

def save_flag(cc, eid):
    FLAGS[cc.upper()] = str(eid)
    _save_json(FLAGS_FILE, FLAGS)

# ═══════════════════════════════════════════════════════════════════════════
#  EMOJI HELPERS
# ═══════════════════════════════════════════════════════════════════════════
def flag_emoji(cc):
    cc = (cc or "").upper()
    if len(cc) != 2 or not cc.isalpha():
        return "🏳"
    return chr(0x1F1E6 + ord(cc[0]) - 65) + chr(0x1F1E6 + ord(cc[1]) - 65)

def pe(key):
    """Return premium-emoji HTML tag for a key (falls back to plain char)."""
    char, eid = PREMIUM_EMOJI.get(key, (key, None))
    if USE_PREMIUM and eid:
        return '<tg-emoji emoji-id="%s">%s</tg-emoji>' % (eid, char)
    return char

def EID(key):
    """Return just the custom-emoji ID for a key (used for button icons)."""
    v = PREMIUM_EMOJI.get(key)
    return v[1] if v else None

def flag_eid(cc):
    """Custom-emoji ID for a country flag (admin capture > FlagsEmoji2024 pack)."""
    cc = (cc or "").upper()
    if len(cc) == 2 and cc.isalpha():
        eid = FLAGS.get(cc) or FLAGS_PACK.get(flag_emoji(cc))
        if eid:
            return str(eid)
    return None

def pe_flag(cc):
    """Premium flag emoji if configured, else unicode flag."""
    uni = flag_emoji(cc)
    eid = FLAGS.get((cc or "").upper()) or FLAGS_PACK.get(uni)
    if USE_PREMIUM and eid:
        return '<tg-emoji emoji-id="%s">%s</tg-emoji>' % (eid, uni)
    return uni

def strip_premium(text):
    return re.sub(r'<tg-emoji emoji-id="[^"]*">(.*?)</tg-emoji>', r'\1', text)

def esc(s):
    return html.escape(str(s if s is not None else ""))

# ══════════════════════════════════════════════════════════════════════════
#  COLORFUL BUTTONS  (Bot API 9.4 : style = success | primary | danger)
#  + premium emoji icon on buttons (icon_custom_emoji_id)
# ══════════════════════════════════════════════════════════════════════════
BTN_SUCCESS = "success"   # green
BTN_PRIMARY = "primary"   # blue
BTN_DANGER  = "danger"    # red

# auto-pick a colour from the button label so the whole UI looks alive
_SUCCESS_WORDS = ("browse", "buy", "confirm", "purchase", "add balance", "deposit",
                  "approve", "i have paid", "paid", "accept", "continue", "yes,",
                  "i have logged in", "joined", "credit", "add fund", "top up",
                  "✅", "💳", "🛒", "💰")
_DANGER_WORDS  = ("cancel", "reject", "remove", "delete", "no,", "not logged in",
                  "decline", "ban", "❌", "🚫", "🗑")
_PRIMARY_WORDS = ("back", "main menu", "prev", "next", "home", "orders", "profile",
                  "support", "channel", "terms", "settings", "admin", "panel",
                  "list", "force join", "emoji", "photo", "broadcast", "deposits",
                  "menu", "⬅", "➡", "🏠", "⚙", "🛡", "🌐", "📜", "📋", "🔗")

def _auto_style(text):
    t = (text or "").lower()
    if any(w in t for w in _DANGER_WORDS):
        return BTN_DANGER
    if any(w in t for w in _SUCCESS_WORDS):
        return BTN_SUCCESS
    if any(w in t for w in _PRIMARY_WORDS):
        return BTN_PRIMARY
    return None

# unicode-emoji matcher (pictographs, symbols, dingbats, arrows, keycaps, flags)
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U00002B00-\U00002BFF"
    "\U00002300-\U000023FF"
    "\U00002190-\U000021FF"
    "\U00002100-\U0000214F"
    "\U000024C2"
    "\U0000200D"
    "\U000020E3"
    "\U0000FE0F"
    "]+",
    flags=re.UNICODE,
)

# map a unicode emoji on a button label -> premium emoji key
_ICON_MAP = {
    "\U0001F6D2": "cart", "\U0001F4B3": "card", "\U0001F4E6": "box", "\U0001F464": "user",
    "\U0001F6E1": "shield", "\U0001F310": "globe", "\U0001F4DC": "doc", "\U0001F3E0": "home",
    "\u2705": "check", "\u274c": "cross", "\u2795": "plus", "\u2796": "minus", "\u2699": "gear",
    "\U0001F3A8": "gem", "\U0001F5BC": "camera", "\U0001F4E3": "megaphone", "\U0001F517": "link",
    "\U0001F5D1": "no", "\u2b05": "back", "\u27a1": "arrow", "\U0001F451": "crown",
    "\U0001F4B0": "money", "\U0001F4CA": "chart", "\U0001F512": "lock", "\U0001F381": "gift",
    "\u26a1": "bolt", "\u23f0": "clock", "\U0001F4F1": "phone", "\U0001F48E": "gem",
    "\u2b50": "star", "\u2728": "sod", "\U0001F50D": "search", "\U0001F4DD": "memo",
    "\U0001F4C4": "doc", "\U0001F3AF": "target", "\U0001F3E6": "bank", "\U0001F194": "id",
    "\U0001F511": "key", "\u26a0": "warn", "\u2139": "info", "\U0001F680": "rocket",
    "\U0001F4CB": "memo", "\U0001F465": "users", "\u270f": "pencil", "\U0001F4DE": "call",
    "\U0001F6AB": "no", "\u2753": "question", "\u2757": "excl", "\U0001F6CD": "bag",
    "\U0001F4C8": "chart2", "\U0001F4BB": "laptop", "\U0001F4D6": "book", "\U0001F504": "refresh",
    "\u2b07": "down", "\u2b06": "up", "\u2764": "heart", "\U0001F44D": "thumbsup",
    "\U0001F44F": "clap", "\U0001F4C1": "folder", "\U0001F319": "moon", "\u2600": "sun",
    "\U0001F308": "rainbow", "\U0001F339": "flower", "\U0001F34F": "apple", "\U0001F370": "cake",
    "\u2615": "coffee", "\u2708": "plane", "\U0001F697": "car", "\U0001F4CC": "pin",
    "\U0001F4CD": "location", "\U0001F4F8": "camera", "\U0001F3A5": "video", "\u2709": "mail",
    "\U0001F4AC": "chat", "\U0001F31F": "star2", "\U0001F449": "point", "\U0001F3F7": "tag",
    "\U0001F4CE": "clip", "\U0001F355": "pizza", "\U0001F3E2": "building", "\U0001F4FA": "tv",
    "\U0001F3AB": "ticket", "\U0001F4F2": "mobile_arrow", "\U0001F4B8": "money_wing",
    "\U0001F4E5": "inbox", "\U0001FA9A": "id2", "\U0001F5DD": "key2", "\U0001F4C9": "chart3",
    "\U0001F4B5": "dollar", "\u2714": "check2", "\u2716": "cross2", "\U0001F4AB": "glow",
    "\U0001F4A5": "boom", "\U0001F48D": "ring", "\U0001F947": "medal2", "\U0001F4C2": "folder2",
    "\U0001F5D2": "note", "\U0001F4C5": "calendar", "\U0001F550": "clock2", "\u231b": "hourglass",
    "\U0001F510": "lock2", "\U0001F513": "unlock", "\U0001F527": "wrench", "\U0001F528": "hammer",
    "\U0001F6E0": "tool", "\U0001F515": "belloff", "\U0001F4E2": "horn", "\u26d3": "chain",
    "\U0001F30D": "world", "\U0001F9D1": "admin", "\u260e": "telephone", "\U0001F4E1": "satellite",
    "\U0001F4AD": "thought", "\U0001F441": "eye", "\U0001F50E": "search2", "\U0001F4B2": "price",
    "\U0001F516": "id", "\U0001F3C6": "trophy", "\U0001F3C5": "medal", "\U0001F514": "bell",
    "\U0001F9FE": "doc", "\U0001FA99": "money",
}

def _first_emoji(text):
    m = _EMOJI_RE.search(text or "")
    return m.group(0) if m else None

def _icon_for(text):
    """Return the premium-emoji id matching the first emoji in a button label."""
    emo = _first_emoji(text)
    if not emo:
        return None
    base = emo.replace("\ufe0f", "").replace("\u20e3", "")
    key = _ICON_MAP.get(emo) or _ICON_MAP.get(base) or _ICON_MAP.get(base[:1])
    if not key:
        return None
    return PREMIUM_EMOJI.get(key, (None, None))[1]

def _clean_btn_text(text):
    """Remove every plain unicode emoji from a button label."""
    if not text:
        return text
    cleaned = _EMOJI_RE.sub("", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return cleaned or text

def IB(text, style=None, icon=None, clean=True, **kw):
    """InlineKeyboardButton with a colour style + premium-emoji icon.

    Plain unicode emojis are stripped from the label and shown only as a
    premium emoji icon, so no 'normal' emoji is ever visible on a button.
    """
    if style is None:
        style = _auto_style(text)
    if icon is None:
        icon = _icon_for(text)
    if clean:
        text = _clean_btn_text(text)
    extra = {}
    if style:
        extra["style"] = style
    if icon and USE_PREMIUM:
        extra["icon_custom_emoji_id"] = str(icon)
    try:
        return types.InlineKeyboardButton(text=text, **extra, **kw)
    except TypeError:
        return types.InlineKeyboardButton(text=text, **kw)

# ═══════════════════════════════════════════════════════════════════════════
#  DATABASE
# ═══════════════════════════════════════════════════════════════════════════
_db_lock = threading.RLock()

def db():
    con = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=20)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with _db_lock:
        con = db()
        c = con.cursor()
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            username TEXT, first_name TEXT,
            balance REAL DEFAULT 0,
            points INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS accounts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT, api_id TEXT, api_hash TEXT, session TEXT,
            country TEXT, cc TEXT, code TEXT,
            price REAL DEFAULT 0,
            status TEXT DEFAULT 'pending',      -- pending | available | sold | disabled
            added_by INTEGER, added_at TEXT,
            sold_to INTEGER, sold_at TEXT
        );
        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, account_id INTEGER,
            price REAL, status TEXT DEFAULT 'awaiting_login',
            otp TEXT, login_confirmed INTEGER DEFAULT 0,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS deposits(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, amount REAL, utr TEXT,
            status TEXT DEFAULT 'pending',      -- pending | approved | rejected
            created_at TEXT, credited_at TEXT
        );
        CREATE TABLE IF NOT EXISTS force_join(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link TEXT, name TEXT, added_at TEXT
        );
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        """)
        con.commit()
        for k, v in (("min_deposit", "20"), ("upi_id", "qureshiotp@upi"),
                     ("upi_name", STORE_NAME), ("menu_photo", ""),
                     ("welcome_photo", ""),
                     # Left empty on purpose: start_public_tunnel() fills this with a
                     # real public URL (Cloudflare) at boot. PUBLIC_BASE_URL env wins.
                     ("api_base_url", "")):
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        for col, typ in (("api_key", "TEXT"), ("referred_by", "INTEGER"),
                         ("ref_counted", "INTEGER DEFAULT 0"),
                         ("ref_count", "INTEGER DEFAULT 0"),
                         ("ref_earned", "REAL DEFAULT 0"),
                         ("accepted_terms", "INTEGER DEFAULT 0")):
            try:
                c.execute("ALTER TABLE users ADD COLUMN %s %s" % (col, typ))
            except Exception:
                pass
        try:
            c.execute("ALTER TABLE accounts ADD COLUMN label TEXT")
        except Exception:
            pass
        try:
            c.execute("ALTER TABLE accounts ADD COLUMN server INTEGER DEFAULT 1")
        except Exception:
            pass
        try:
            c.execute("ALTER TABLE accounts ADD COLUMN platform TEXT DEFAULT 'telegram'")
        except Exception:
            pass
        try:
            c.execute("ALTER TABLE accounts ADD COLUMN seller_id INTEGER DEFAULT 0")
        except Exception:
            pass
        c.execute("""CREATE TABLE IF NOT EXISTS referrals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER, referred_id INTEGER,
            reward REAL, created_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS redeem_codes(
            code TEXT PRIMARY KEY,
            amount REAL,
            max_uses INTEGER DEFAULT 1,
            uses INTEGER DEFAULT 0,
            used_by TEXT DEFAULT '',
            created_at TEXT,
            active INTEGER DEFAULT 1)""")
        for k, v in (("referral_reward", "1"), ("referral_enabled", "1"),
                     ("qr_photo", ""), ("qr_mode", "static"),
                     ("force_terms", "1"),
                     ("proof_channel", ""), ("proof_enabled", "1"),
                     # ── TGShark API (Server 1) pricing — admin configurable ──
                     ("api_price", "0"),          # global ₹ price for API accounts (0 = use auto)
                     ("api_usd_rate", "90"),      # ₹ per $1 (for auto conversion)
                     ("api_markup", "1.5"),       # multiplier on the API price
                     ("api_country_prices", "{}"),# JSON {country_code: price} overrides
                     ("api_enabled", "1")):
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        for col, typ in (("api_hash", "TEXT"), ("api_phone", "TEXT"),
                         ("api_country", "TEXT"), ("is_api", "INTEGER DEFAULT 0")):
            try:
                c.execute("ALTER TABLE orders ADD COLUMN %s %s" % (col, typ))
            except Exception:
                pass
        con.commit()
        con.close()

def get_setting(key, default=None):
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("SELECT value FROM settings WHERE key=?", (key,))
        r = c.fetchone(); con.close()
        return r["value"] if r else default

def set_setting(key, value):
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, str(value)))
        con.commit(); con.close()

def _valid_public_base(url):
    """Return a clean, real public base URL (no trailing slash) or '' if it is a
    placeholder / local / empty value. Used so the Developer API never shows the
    old 'your-domain' text and always exposes a genuinely openable link."""
    if not url or not isinstance(url, str):
        return ""
    u = url.strip().rstrip("/")
    low = u.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        return ""
    for bad in ("your-domain", "example.com", "localhost", "127.0.0.1", "0.0.0.0"):
        if bad in low:
            return ""
    return u

def get_api_base():
    """Public REST base ending in /api/v1, or '' while the tunnel is provisioning.
    PUBLIC_BASE_URL env var (e.g. your own Cloudflare domain) takes priority."""
    env = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if env:
        base = _valid_public_base(env)
        if base:
            return base if base.endswith("/api/v1") else base + "/api/v1"
    return _valid_public_base(get_setting("api_base_url", "") or "")

def get_public_root():
    """Public site root (no /api/v1) used to build the per-user web-store link."""
    base = get_api_base()
    if base.endswith("/api/v1"):
        return base[:-len("/api/v1")].rstrip("/")
    return base


def ensure_user(u):
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("SELECT user_id FROM users WHERE user_id=?", (u.id,))
        if not c.fetchone():
            c.execute("INSERT INTO users(user_id,username,first_name,balance,points,created_at) VALUES(?,?,?,?,?,?)",
                      (u.id, u.username or "", u.first_name or "", 0, 0, datetime.utcnow().isoformat()))
            con.commit()
        else:
            c.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?",
                      (u.username or "", u.first_name or "", u.id))
            con.commit()
        con.close()

def get_user(uid):
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("SELECT * FROM users WHERE user_id=?", (uid,))
        r = c.fetchone(); con.close()
        return dict(r) if r else None

def add_balance(uid, amount):
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, uid))
        con.commit(); con.close()

def money(v):
    try:
        v = float(v)
    except Exception:
        v = 0.0
    if v == int(v):
        return "%s%s" % (CURRENCY, int(v))
    return "%s%.2f" % (CURRENCY, v)

def _count(sql, args=()):
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute(sql, args); r = c.fetchone(); con.close()
        return r["n"] if r else 0

# ══════════════════════════════════════════════════════════════════════════════
#  TGSHARK API  (Server 1 = auto-fetched accounts)
#  Docs: https://tgsharkapi.store/docs/
#  Auth: query-string apiKey (or X-API-Key header) + action param.
#  Actions: getBalance, getCountrys/getCountries, getInfo,
#           getNumber&country=XX, getCode&hash_code=HASH, wallet
# ══════════════════════════════════════════════════════════════════════════════
def _tgshark(action, timeout=25, **params):
    """Call the TGShark API and return the parsed JSON dict (or {'status':'error'}).

    Never raises — network/JSON problems are reported through the 'status' key
    so callers can degrade gracefully without crashing the bot.
    """
    if not TGSHARK_API_KEY:
        return {"status": "error", "message": "TGShark API key not configured"}
    q = {"apiKey": TGSHARK_API_KEY, "action": action}
    for k, v in params.items():
        if v is not None:
            q[k] = v
    try:
        r = requests.get(TGSHARK_BASE + "/", params=q,
                         headers={"X-API-Key": TGSHARK_API_KEY,
                                  "User-Agent": "QureshiOTP/1.0"},
                         timeout=timeout)
        try:
            data = r.json()
        except Exception:
            return {"status": "error", "message": "Invalid API response (HTTP %s)" % r.status_code}
        if isinstance(data, dict):
            data.setdefault("_http", r.status_code)
            return data
        return {"status": "error", "message": "Unexpected API payload"}
    except Exception as e:
        return {"status": "error", "message": "Network error: %s" % e}

def tgshark_balance():
    return _tgshark("getBalance")

_CC_NAME = {
    "AZ": "Azerbaijan", "BD": "Bangladesh", "BR": "Brazil", "CG": "Congo", "CL": "Chile",
    "CN": "China", "CO": "Colombia", "DZ": "Algeria", "EC": "Ecuador", "EG": "Egypt",
    "GB": "United Kingdom", "HR": "Croatia", "ID": "Indonesia", "IE": "Ireland",
    "IN": "India", "IQ": "Iraq", "IR": "Iran", "IT": "Italy", "JO": "Jordan",
    "JP": "Japan", "KE": "Kenya", "KG": "Kyrgyzstan", "KH": "Cambodia", "KZ": "Kazakhstan",
    "LK": "Sri Lanka", "MA": "Morocco", "MG": "Madagascar", "MM": "Myanmar", "MW": "Malawi",
    "MX": "Mexico", "MY": "Malaysia", "NE": "Niger", "NG": "Nigeria", "NI": "Nicaragua",
    "NP": "Nepal", "OM": "Oman", "PA": "Panama", "PK": "Pakistan", "PL": "Poland",
    "RU": "Russia", "SA": "Saudi Arabia", "SD": "Sudan", "SG": "Singapore", "SN": "Senegal",
    "SO": "Somalia", "TH": "Thailand", "TN": "Tunisia", "US": "United States",
    "VE": "Venezuela", "VN": "Vietnam", "YE": "Yemen",
}

def _cc_from_flag(s):
    """Return the ISO-2 code from a leading regional-indicator flag, else ''."""
    m = re.search(r'([\U0001F1E6-\U0001F1FF]{2})', s or "")
    if not m:
        return ""
    f = m.group(1)
    return "".join(chr(ord(ch) - 0x1F1E6 + 65) for ch in f)

def tgshark_countries():
    """Return a normalised list of country dicts:
    {country, cc, dial, name, count, min_price, max_price, iso}
    """
    data = _tgshark("getCountrys")
    if str(data.get("status")) != "ok":
        data = _tgshark("getCountries")
    if str(data.get("status")) != "ok":
        return []
    raw = data.get("countries") or data.get("countrys") or data.get("data") or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    out = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        iso = str(it.get("iso") or it.get("name") or "")
        cc = _cc_from_flag(iso)
        if not cc:
            m = re.search(r'\b([A-Z]{2})\b', iso)
            cc = m.group(1) if m else ""
        dial = ""
        m2 = re.search(r'\+\s*(\d+)', iso)
        if m2:
            dial = "+" + m2.group(1)
        # clean human label = iso minus flag, dial and trailing price
        label = re.sub(r'[\U0001F1E6-\U0001F1FF]{2}', '', iso)
        label = re.sub(r'\+\s*\d+', '', label)
        label = label.split("|")[0]
        label = re.sub(r'[\$]', '', label)
        label = re.sub(r'[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF'
                       r'\u2300-\u23FF\u2190-\u21FF\u2100-\u214F\uFE0F\u200D]', '', label)
        label = re.sub(r'\s{2,}', ' ', label).strip(" \u2022-|()")
        base = _CC_NAME.get(cc)
        variant = label
        if cc:
            variant = re.sub(r'\b' + re.escape(cc) + r'\b', '', variant)
        variant = re.sub(r'[()]', ' ', variant).strip(" \u2022-|")
        if base:
            if base.lower() in label.lower():
                name = base
            else:
                name = (base + (" " + variant if variant else "")).strip()
        else:
            name = label or cc or ("Country " + str(it.get("country")))
        try:
            count = int(it.get("count") or 0)
        except Exception:
            count = 0
        try:
            minp = float(it.get("min_price") or 0)
        except Exception:
            minp = 0.0
        try:
            maxp = float(it.get("max_price") or 0)
        except Exception:
            maxp = 0.0
        out.append({"country": str(it.get("country") or ""), "cc": cc, "dial": dial,
                    "name": name, "count": count, "min_price": minp,
                    "max_price": maxp, "iso": iso})
    out.sort(key=lambda x: x["name"].lower())
    return out

def tgshark_buy(country):
    """Purchase a number for a country. Returns the API JSON dict."""
    return _tgshark("getNumber", country=country)

def tgshark_code(hash_code):
    """Poll for the login code for a purchased hash. Returns API JSON dict."""
    return _tgshark("getCode", hash_code=hash_code, timeout=20)

def tgshark_info():
    return _tgshark("getInfo")

# ── API price resolution (admin-configurable) ───────────────────────────────
def _api_country_prices():
    try:
        return json.loads(get_setting("api_country_prices", "{}") or "{}")
    except Exception:
        return {}

def api_price_for(country, cc="", min_price_usd=0.0):
    """Resolve the ₹ selling price for an API account.

    Priority:  per-country override  ->  global default (api_price)  ->
    API price (usd * rate) + profit.  Returns (price, source).
    """
    prices = _api_country_prices()
    for key in (str(country), (cc or "").upper()):
        if key and key in prices:
            try:
                return float(prices[key]), "override"
            except Exception:
                pass
    try:
        default = float(get_setting("api_price", "0") or 0)
    except Exception:
        default = 0.0
    if default > 0:
        return default, "default"
    try:
        rate = float(get_setting("api_usd_rate", "90") or 90)
    except Exception:
        rate = 90.0
    try:
        profit = float(get_setting("api_profit", "0") or 0)
    except Exception:
        profit = 0.0
    if min_price_usd and min_price_usd > 0:
        import math
        base = min_price_usd * rate            # same as API price (converted to ₹)
        sell = base * (1.0 + profit)           # + admin profit
        return float(math.ceil(sell)), "auto"
    return 0.0, "unset"

# ═══════════════════════════════════════════════════════════════════════════
#  BOT
# ═══════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
#  DEVELOPER API  (per-user secret key -> their own balance)
# ══════════════════════════════════════════════════════════════════════════════
def _gen_api_key(uid):
    body = ''.join(random.choices(string.ascii_letters + string.digits, k=28))
    return "elt_live_%s_%s" % (uid, body)

def get_or_create_api_key(uid):
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("SELECT api_key FROM users WHERE user_id=?", (uid,))
        r = c.fetchone()
        if r and r["api_key"]:
            key = r["api_key"]; con.close(); return key
        key = _gen_api_key(uid)
        c.execute("UPDATE users SET api_key=? WHERE user_id=?", (key, uid))
        con.commit(); con.close()
        return key

def regenerate_api_key(uid):
    key = _gen_api_key(uid)
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("UPDATE users SET api_key=? WHERE user_id=?", (key, uid))
        con.commit(); con.close()
    return key

def user_by_api_key(key):
    if not key:
        return None
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("SELECT * FROM users WHERE api_key=?", (str(key),))
        r = c.fetchone(); con.close()
        return dict(r) if r else None


class _BotErrorHandler(telebot.ExceptionHandler):
    """Log every uncaught handler error and (best-effort) alert the owner.

    Without this, an exception inside any callback/message handler is swallowed
    by the polling loop and the user just sees the button spinner stop — which
    looks like a silent "server error"."""

    def handle(self, exception):
        try:
            log.error("handler exception: %s", exception, exc_info=True)
        except Exception:
            pass
        try:
            tb = "".join(traceback.format_exception(
                type(exception), exception, exception.__traceback__))
            safe_send(OWNER_ID,
                      f"{pe('cross')} <b>Bot error caught</b>\n{DIV}\n"
                      f"<code>{esc(tb[-1200:])}</code>")
        except Exception:
            pass
        return True

# Placeholder token keeps the module importable when BOT_TOKEN is blank;
# main() refuses to start until a real token is supplied.
bot = telebot.TeleBot(BOT_TOKEN or "0:PLACEHOLDER_SET_BOT_TOKEN",
                      parse_mode="HTML", exception_handler=_BotErrorHandler())

def referral_link(uid):
    uname = BOT_USERNAME or (get_setting("bot_username", "") or "")
    if not uname:
        try:
            uname = bot.get_me().username
        except Exception:
            uname = ""
    return "https://t.me/%s?start=ref_%s" % (uname, uid)

def credit_referral(referred_uid):
    """Credit the referrer once the invited user has joined force-join."""
    if str(get_setting("referral_enabled", "1")) == "0":
        return
    reward = float(get_setting("referral_reward", "1") or 1)
    referrer = None
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("SELECT referred_by, ref_counted FROM users WHERE user_id=?", (referred_uid,))
        r = c.fetchone()
        if not r or not r["referred_by"] or r["ref_counted"]:
            con.close(); return
        referrer = int(r["referred_by"])
        c.execute("UPDATE users SET ref_counted=1 WHERE user_id=?", (referred_uid,))
        c.execute("UPDATE users SET balance=balance+?, ref_count=ref_count+1, "
                  "ref_earned=ref_earned+? WHERE user_id=?", (reward, reward, referrer))
        c.execute("INSERT INTO referrals(referrer_id,referred_id,reward,created_at) "
                  "VALUES(?,?,?,?)",
                  (referrer, referred_uid, reward, datetime.utcnow().isoformat()))
        con.commit(); con.close()
    try:
        safe_send(referrer,
                  f"{pe('gift')} <b>Referral Reward!</b>\n{DIV}\n"
                  f"{pe('user')} A user you invited joined the channel.\n"
                  f"{pe('money')} You earned <b>{money(reward)}</b>!\n"
                  f"{pe('bolt')} Keep sharing to earn more.")
    except Exception:
        pass

def is_admin(uid):
    return uid in ADMIN_IDS or uid == OWNER_ID

def safe_send(chat_id, text, reply_markup=None, photo=None, **kw):
    """Send with premium emoji; on failure retry with plain text."""
    try:
        if photo:
            return bot.send_photo(chat_id, photo, caption=text, reply_markup=reply_markup, **kw)
        return bot.send_message(chat_id, text, reply_markup=reply_markup, disable_web_page_preview=True, **kw)
    except Exception as e:
        log.warning("safe_send premium failed (%s) -> plain retry", e)
        try:
            plain = strip_premium(text)
            if photo:
                return bot.send_photo(chat_id, photo, caption=plain, reply_markup=reply_markup, **kw)
            return bot.send_message(chat_id, plain, reply_markup=reply_markup, disable_web_page_preview=True, **kw)
        except Exception as e2:
            log.error("safe_send plain also failed: %s", e2)
            return None

def safe_edit(chat_id, message_id, text, reply_markup=None):
    try:
        return bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup,
                                     disable_web_page_preview=True)
    except Exception as e:
        if "message is not modified" in str(e).lower():
            return None
        try:
            return bot.edit_message_text(strip_premium(text), chat_id, message_id,
                                         reply_markup=reply_markup, disable_web_page_preview=True)
        except Exception:
            return None

DIV  = "━" * 24
DIV2 = "─" * 22

# ═══════════════════════════════════════════════════════════════════════════
#  FORCE JOIN
# ═══════════════════════════════════════════════════════════════════════════
def get_force_join():
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("SELECT * FROM force_join ORDER BY id")
        rows = [dict(r) for r in c.fetchall()]; con.close()
        return rows

def user_joined(uid):
    """Return True if user is in ALL force-join channels (or none set)."""
    channels = get_force_join()
    if not channels:
        return True
    for ch in channels:
        try:
            m = bot.get_chat_member(ch["link"], uid)
            if m.status in ("left", "kicked"):
                return False
        except Exception:
            # if bot can't check, don't block the user
            continue
    return True

def force_join_markup():
    kb = types.InlineKeyboardMarkup(row_width=1)
    for ch in get_force_join():
        kb.add(IB(text=ch["name"], url=ch["link"], clean=False))
    kb.add(IB(
        text="✅ Continue to Purchase • I have joined", callback_data="fj_check"))
    return kb

def force_join_text():
    return (f"{pe('lock')} <b>Join Required</b>\n"
            f"{DIV}\n"
            f"{pe('shield')} <i>Please join our official channel(s) below to continue.</i>\n\n"
            f"{pe('point')} Tap each button, join, then press "
            f"<b>Continue to Purchase</b>.")

# ═══════════════════════════════════════════════════════════════════════════
#  MAIN MENU
# ═══════════════════════════════════════════════════════════════════════════
def main_menu_text(u):
    usr = get_user(u.id) or {}
    bal = money(usr.get("balance", 0))
    total_accounts = _count("SELECT COUNT(*) n FROM accounts WHERE status='available'")
    total_countries = _count("SELECT COUNT(DISTINCT country) n FROM accounts WHERE status='available'")
    name = esc(u.first_name or "User")
    return (
        f"{pe('crown')} <b>{esc(STORE_NAME)} {esc(STORE_TAG)}</b> {pe('star')}\n"
        f"{pe('sod')}\n"
        f"<blockquote>"
        f"{pe('cart')} <b>Buy Now</b>\n"
        f"{pe('card')} Add Balance\n"
        f"{pe('box')} My Orders\n"
        f"{pe('user')} My Profile\n"
        f"{pe('shield')} 24/7 Support\n"
        f"{pe('globe')} Get Channel\n"
        f"{pe('bag')} Store Items"
        f"</blockquote>\n"
        f"{pe('sod')}\n"
        f"{pe('wave')} Hey <b>{name}</b>, welcome back!\n"
        f"{pe('money')} <b>Your Balance:</b> <code>{bal}</code>\n"
        f"{pe('bank')} <i>Select Bank / Wallet</i>\n"
        f"{DIV}\n"
        f"{pe('check')} <b>Verified Accounts &amp; Session Files</b>\n"
        f"{pe('bolt')} Instant automated delivery \u00b7 24/7 support\n"
        f"{pe('chart')} <b>{total_accounts}</b> accounts \u00b7 <b>{total_countries}</b> countries in stock"
    )

def main_menu_kb(uid=None):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(IB("BROWSE ACCOUNTS", callback_data="browse", style=BTN_SUCCESS, icon=EID("cart")))
    kb.add(IB("ADD BALANCE", callback_data="addbal", style=BTN_SUCCESS, icon=EID("card")),
           IB("MY ORDERS", callback_data="orders", style=BTN_PRIMARY, icon=EID("box")))
    kb.add(IB("MY PROFILE", callback_data="profile", style=BTN_PRIMARY, icon=EID("user")),
           IB("SUPPORT", url=SUPPORT_URL, style=BTN_PRIMARY, icon=EID("shield")))
    kb.add(IB("OFFICIAL CHANNEL", url=CHANNEL_URL, style=BTN_PRIMARY, icon=EID("globe")),
           IB("PAYMENT PROOFS", callback_data="proofs", style=BTN_PRIMARY, icon=EID("doc")))
    kb.add(IB("REFER & EARN", callback_data="referral", style=BTN_SUCCESS, icon=EID("gift")),
           IB("REDEEM CODE", callback_data="redeem", style=BTN_SUCCESS, icon=EID("gem")))
    kb.add(IB("TERMS OF SERVICE", callback_data="terms", style=BTN_DANGER, icon=EID("lock")))
    if uid is not None and is_admin(uid):
        kb.add(IB("ADMIN PANEL", callback_data="adm_menu", style=BTN_DANGER, icon=EID("crown")))
    return kb

def send_main_menu(chat_id, u, edit_message_id=None):
    photo = get_setting("menu_photo", "") or ""
    txt = main_menu_text(u)
    kb = main_menu_kb(getattr(u, "id", None))
    if edit_message_id:
        safe_edit(chat_id, edit_message_id, txt, kb)
    else:
        if photo:
            safe_send(chat_id, txt, reply_markup=kb, photo=photo)
        else:
            safe_send(chat_id, txt, reply_markup=kb)

# ═══════════════════════════════════════════════════════════════════════════
#  /start
# ═══════════════════════════════════════════════════════════════════════════
@bot.message_handler(commands=["start"])
def cmd_start(m):
    ensure_user(m.from_user)
    u = get_user(m.from_user.id)
    if u and u.get("banned"):
        return safe_send(m.chat.id, f"{pe('cross')} You are banned from using this store.")
    if maintenance_on() and not is_admin(m.from_user.id):
        return safe_send(m.chat.id,
                         f"{pe('tool')} <b>Under Maintenance</b>\n{DIV}\n"
                         f"{pe('info')} We are upgrading the store right now.\n"
                         f"{pe('clock')} Please try again in a little while.")
    # capture referral from deep-link:  /start ref_<uid>
    try:
        parts = (m.text or "").split()
        if len(parts) > 1 and parts[1].startswith("ref"):
            ref = int(re.sub(r'\D', '', parts[1]) or 0)
            if ref and ref != m.from_user.id:
                with _db_lock:
                    con = db(); c = con.cursor()
                    c.execute("SELECT referred_by FROM users WHERE user_id=?", (m.from_user.id,))
                    row = c.fetchone()
                    if row and not row["referred_by"]:
                        c.execute("UPDATE users SET referred_by=? WHERE user_id=?",
                                  (ref, m.from_user.id))
                        con.commit()
                    con.close()
    except Exception:
        pass
    if not user_joined(m.from_user.id):
        return safe_send(m.chat.id, force_join_text(), reply_markup=force_join_markup())
    credit_referral(m.from_user.id)
    send_main_menu(m.chat.id, m.from_user)

@bot.message_handler(commands=["id", "myid"])
def cmd_id(m):
    safe_send(m.chat.id, f"{pe('user')} Your ID: <code>{m.from_user.id}</code>\n"
                         f"{pe('globe')} Chat ID: <code>{m.chat.id}</code>")

@bot.callback_query_handler(func=lambda c: c.data == "fj_check")
def cb_fj_check(c):
    if not user_joined(c.from_user.id):
        bot.answer_callback_query(c.id, "You haven't joined yet!", show_alert=True)
        return
    bot.answer_callback_query(c.id, "Verified — welcome!")
    credit_referral(c.from_user.id)
    try:
        bot.delete_message(c.message.chat.id, c.message.message_id)
    except Exception:
        pass
    send_main_menu(c.message.chat.id, c.from_user)

@bot.callback_query_handler(func=lambda c: c.data == "home")
def cb_home(c):
    bot.answer_callback_query(c.id)
    send_main_menu(c.message.chat.id, c.from_user, edit_message_id=c.message.message_id)

# ═══════════════════════════════════════════════════════════════════════════
#  BROWSE ACCOUNTS
# ═══════════════════════════════════════════════════════════════════════════
PLATFORMS = {1: "telegram", 2: "whatsapp"}
PLATFORM_LABEL = {"telegram": "Telegram", "whatsapp": "WhatsApp"}
PLATFORM_EMOJI = {"telegram": "\u2708\ufe0f", "whatsapp": "\U0001F4AC"}

def country_rows(platform=None):
    with _db_lock:
        con = db(); c = con.cursor()
        if platform:
            c.execute("""SELECT country, cc, code, COUNT(*) n, MIN(price) minp
                         FROM accounts WHERE status='available' AND COALESCE(platform,'telegram')=?
                         GROUP BY country ORDER BY country""", (platform,))
        else:
            c.execute("""SELECT country, cc, code, COUNT(*) n, MIN(price) minp
                         FROM accounts WHERE status='available'
                         GROUP BY country ORDER BY country""")
        rows = [dict(r) for r in c.fetchall()]; con.close()
        return rows

def browse_text(u=None, platform="telegram"):
    bal = money((get_user(u.id) or {}).get("balance", 0)) if u else money(0)
    total = _count("SELECT COUNT(*) n FROM accounts WHERE status='available' AND COALESCE(platform,'telegram')=?", (platform,))
    countries = _count("SELECT COUNT(DISTINCT country) n FROM accounts WHERE status='available' AND COALESCE(platform,'telegram')=?", (platform,))
    s1 = _count("SELECT COUNT(*) n FROM accounts WHERE status='available' AND COALESCE(platform,'telegram')=? AND COALESCE(server,1)=1", (platform,))
    s2 = _count("SELECT COUNT(*) n FROM accounts WHERE status='available' AND COALESCE(platform,'telegram')=? AND COALESCE(server,1)=2", (platform,))
    pemoji = PLATFORM_EMOJI.get(platform, "")
    pname = PLATFORM_LABEL.get(platform, "Telegram")
    return (
        f"{pemoji} <b>{esc(STORE_NAME)}</b> \u2022 <b>{pname} Accounts</b>\n"
        f"{DIV}\n"
        f"<blockquote>{pe('box')} <b>Stock:</b> <b>{total}</b> Available\n"
        f"{pe('wallet')} <b>Your Balance:</b> <code>{bal}</code>\n"
        f"{pe('globe')} <b>Countries:</b> <b>{countries}</b>\n"
        f"{SERVER_EMOJI[1]} <b>Server 1:</b> {s1} (No guarantee)  \u2022  {SERVER_EMOJI[2]} <b>Server 2:</b> {s2} (Guaranteed)\n"
        f"{pe('chart')} <b>Page:</b> 1</blockquote>\n"
        f"\n"
        f"{pe('point')} <i>Select a country below to browse accounts:</i>"
    )

_ACCT_STYLES = [BTN_SUCCESS, BTN_PRIMARY]

def _acct_style(i):
    return _ACCT_STYLES[i % len(_ACCT_STYLES)]

def _srv_badge(srv):
    srv = int(srv or 1)
    return f"{SERVER_EMOJI.get(srv, '')} S{srv}"

def _srv_disclaimer(srv):
    srv = int(srv or 1)
    if srv == 2:
        return f"{pe('shield')} <b>Server 2</b> \u2022 Premium quality \u2022 <b>Guaranteed</b>"
    return f"{pe('warn')} <b>Server 1</b> \u2022 Cheapest \u2022 <b>No guarantee</b> (sold as-is)"

def _browse_server_chooser(chat_id, mid=None):
    """Top-level chooser: Server 1 / Server 2."""
    kb = types.InlineKeyboardMarkup(row_width=1)
    api_on = str(get_setting("api_enabled", "1")) != "0"
    kb.add(IB("\U0001F7E2 Server 1  \u2022  No Guarantee",
              callback_data="browse_srv_1", style=BTN_SUCCESS, icon=EID("globe")))
    kb.add(IB("\U0001F535 Server 2  \u2022  Guaranteed",
              callback_data="browse_srv_2", style=BTN_PRIMARY, icon=EID("shield")))
    kb.add(IB("MAIN MENU", callback_data="home", style=BTN_DANGER, icon=EID("home")))
    txt = (f"{pe('cart')} <b>Browse Accounts</b>\n{DIV}\n"
           f"{pe('point')} Choose a server to browse:\n\n"
           f"{pe('warn')} <b>Server 1</b> \u2014 Cheapest \u2022 <b>No guarantee</b> (sold as-is).\n"
           f"{pe('shield')} <b>Server 2</b> \u2014 Premium quality \u2022 <b>Guaranteed</b>.")
    if not api_on:
        txt += f"\n\n{pe('info')} <i>Server 1 is temporarily unavailable.</i>"
    if mid:
        safe_edit(chat_id, mid, txt, kb)
    else:
        safe_send(chat_id, txt, reply_markup=kb)

# \u2500\u2500 Server 1: live account feed (no API wording shown to users) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
_API_COUNTRIES_CACHE = {"ts": 0.0, "data": []}
_API_SORT = {}    # chat_id -> "low" | "high"
_API_SEARCH = {}  # chat_id -> query text
_API_BUDGET = {}  # chat_id -> max price

def _api_countries_cached(max_age=60):
    now = time.time()
    if _API_COUNTRIES_CACHE["data"] and (now - _API_COUNTRIES_CACHE["ts"] < max_age):
        return _API_COUNTRIES_CACHE["data"]
    data = tgshark_countries()
    if data:
        _API_COUNTRIES_CACHE["data"] = data
        _API_COUNTRIES_CACHE["ts"] = now
    return data

def _api_find_country(country):
    for c in _api_countries_cached():
        if str(c.get("country")) == str(country):
            return c
    return {}

def _api_countries_grouped():
    """One entry per country (deduped by ISO cc); cheapest variant kept."""
    groups = {}
    for c in _api_countries_cached():
        cc = (c.get("cc") or "").upper()
        key = cc or ("id:" + str(c.get("country")))
        g = groups.get(key)
        mp = float(c.get("min_price") or 0)
        if g is None:
            g = {"key": key, "cc": cc, "dial": c.get("dial") or "",
                 "name": c.get("name") or c.get("country") or cc,
                 "count": 0, "min_price": 0.0, "country": c.get("country"),
                 "variants": []}
            groups[key] = g
        g["variants"].append(c)
        g["count"] += int(c.get("count") or 0)
        if not g["dial"] and c.get("dial"):
            g["dial"] = c.get("dial")
        if g["min_price"] == 0.0 or (mp > 0 and mp < g["min_price"]):
            g["min_price"] = mp
            g["country"] = c.get("country")
            if c.get("dial"):
                g["dial"] = c.get("dial")
    return list(groups.values())

def _api_grid_rows(groups, sort="low", q="", budget=0.0):
    out = []
    for g in groups:
        price, src = api_price_for(g["country"], g["cc"], g["min_price"])
        g = dict(g); g["price"] = price; g["src"] = src
        out.append(g)
    if q:
        ql = q.strip().lower()
        out = [g for g in out if ql in (g["name"] or "").lower()
               or ql in (g["cc"] or "").lower() or ql in str(g.get("dial") or "")]
    if budget and budget > 0:
        out = [g for g in out if 0 < g["price"] <= budget]
    out.sort(key=lambda g: (g["price"] if g["price"] > 0 else 10**9),
             reverse=(sort == "high"))
    return out

def _api_page_row(cur, total, prefix):
    if total <= 1:
        return None
    if total <= 7:
        pages = list(range(total))
    else:
        start = max(0, min(cur - 3, total - 7))
        pages = list(range(start, start + 7))
    return [IB(str(p + 1), callback_data=f"{prefix}{p}",
               style=(BTN_SUCCESS if p == cur else BTN_PRIMARY)) for p in pages]

def _api_browse(chat_id, mid=None, page=0, per_page=10):
    if str(get_setting("api_enabled", "1")) == "0":
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(IB("SWITCH TO SERVER 2", callback_data="browse_srv_2", style=BTN_PRIMARY, icon=EID("shield")))
        kb.add(IB("MAIN MENU", callback_data="home", style=BTN_DANGER, icon=EID("home")))
        txt = (f"{pe('cross')} <b>Server 1 is temporarily unavailable.</b>\n{DIV}\n"
               f"{pe('info')} Please browse Server 2.")
        return safe_edit(chat_id, mid, txt, kb) if mid else safe_send(chat_id, txt, reply_markup=kb)
    groups = _api_countries_grouped()
    if not groups:
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(IB("RETRY", callback_data="browse_srv_1", style=BTN_SUCCESS, icon=EID("refresh")))
        kb.add(IB("SWITCH TO SERVER 2", callback_data="browse_srv_2", style=BTN_PRIMARY, icon=EID("shield")))
        kb.add(IB("MAIN MENU", callback_data="home", style=BTN_DANGER, icon=EID("home")))
        txt = (f"{pe('cross')} <b>No accounts available right now.</b>\n{DIV}\n"
               f"{pe('info')} Please try again shortly.")
        return safe_edit(chat_id, mid, txt, kb) if mid else safe_send(chat_id, txt, reply_markup=kb)
    sort = _API_SORT.get(chat_id, "low")
    q = _API_SEARCH.get(chat_id, "")
    budget = _API_BUDGET.get(chat_id, 0.0)
    rows = _api_grid_rows(groups, sort, q, budget)
    total = len(rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    chunk = rows[page * per_page:(page + 1) * per_page]
    kb = types.InlineKeyboardMarkup(row_width=2)
    pair = []
    for idx, g in enumerate(chunk):
        cc = g["cc"] or ""
        dial = g["dial"] or ""
        ptxt = money(g["price"]) if g["price"] > 0 else "\u2014"
        core = f"{cc}{dial}" if (cc or dial) else (g["name"] or "")[:10]
        label = f"{core} \u2022 {ptxt}"
        pair.append(IB(label, callback_data=f"apic_{g['key']}|{page}",
                       style=_acct_style(idx), icon=flag_eid(cc) or EID("globe")))
        if len(pair) == 2:
            kb.row(*pair); pair = []
    if pair:
        kb.row(*pair)
    pg = _api_page_row(page, total_pages, "apicpg_")
    if pg:
        kb.row(*pg)
    nav = []
    if page > 0:
        nav.append(IB("PREV", callback_data=f"apicpg_{page-1}", style=BTN_PRIMARY, icon=EID("back")))
    if page + 1 < total_pages:
        nav.append(IB("NEXT", callback_data=f"apicpg_{page+1}", style=BTN_PRIMARY, icon=EID("arrow")))
    if nav:
        kb.row(*nav)
    kb.row(IB("\U0001F525 Top 10 Lowest Price Countries", callback_data="apitop10",
              style=BTN_SUCCESS, icon=EID("fire")))
    kb.row(IB("\u00ab Servers", callback_data="browse", style=BTN_PRIMARY, icon=EID("back")),
           IB("Search", callback_data="apisearch", style=BTN_PRIMARY, icon=EID("search")),
           IB("Filters", callback_data="apifilter", style=BTN_PRIMARY, icon=EID("gear")))
    kb.row(IB(("Sort \u2022 High to Low" if sort == "low" else "Sort \u2022 Low to High"),
              callback_data="apisort", style=BTN_SUCCESS, icon=EID("chart")))
    kb.row(IB("TERMS OF SERVICE", callback_data="terms", style=BTN_DANGER, icon=EID("lock")))
    head = f"{SERVER_EMOJI[1]} <b>Server 1</b>"
    extra = []
    if q:
        extra.append(f"search: <b>{esc(q)}</b>")
    if budget and budget > 0:
        extra.append(f"budget: <b>{money(budget)}</b>")
    txt = (f"{head}\n{DIV}\n"
           f"<blockquote>{pe('globe')} <b>Countries:</b> <b>{total}</b>\n"
           f"{pe('chart')} <b>Page:</b> {page+1}/{total_pages}"
           + (("\n" + " \u2022 ".join(extra)) if extra else "") +
           f"</blockquote>\n\n"
           f"{pe('point')} <i>Select a country to browse accounts:</i>")
    if mid:
        safe_edit(chat_id, mid, txt, kb)
    else:
        safe_send(chat_id, txt, reply_markup=kb)

def _api_country_view(chat_id, mid, key, page=0):
    g = None
    for x in _api_countries_grouped():
        if x["key"] == key:
            g = x; break
    if g is None:
        c = _api_find_country(key)
        if c:
            g = {"key": key, "cc": (c.get("cc") or "").upper(), "dial": c.get("dial") or "",
                 "name": c.get("name") or key, "count": int(c.get("count") or 0),
                 "min_price": float(c.get("min_price") or 0), "country": c.get("country"),
                 "variants": [c]}
    if not g:
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(IB("BACK", callback_data="browse_srv_1", style=BTN_PRIMARY, icon=EID("back")))
        kb.add(IB("MAIN MENU", callback_data="home", style=BTN_DANGER, icon=EID("home")))
        return safe_edit(chat_id, mid, f"{pe('cross')} <b>Country not found.</b>", kb)
    cc = g["cc"]
    flag = pe_flag(cc)
    kb = types.InlineKeyboardMarkup(row_width=1)
    variants = sorted(g["variants"], key=lambda x: float(x.get("min_price") or 0))
    best_price = 0.0
    for v in variants:
        price, _src = api_price_for(v["country"], cc, v.get("min_price") or 0)
        if price > 0 and (best_price == 0.0 or price < best_price):
            best_price = price
        cnt = int(v.get("count") or 0)
        if price > 0:
            kb.add(IB(f"BUY ACCOUNT \u2022 {money(price)}  ({cnt} in stock)",
                      callback_data=f"apibuy_{v['country']}", style=BTN_SUCCESS, icon=EID("cart")))
        else:
            kb.add(IB("PRICE NOT SET \u2014 CONTACT SUPPORT", url=SUPPORT_URL,
                      style=BTN_PRIMARY, icon=EID("warn")))
    kb.add(IB("BACK", callback_data=f"apicpg_{page}", style=BTN_PRIMARY, icon=EID("back")),
           IB("MAIN MENU", callback_data="home", style=BTN_DANGER, icon=EID("home")))
    txt = (f"{flag} <b>{esc(g['name'])}</b> \u2022 {SERVER_EMOJI[1]} Server 1\n{DIV}\n"
           f"<blockquote>{pe('phone')} <b>Country Code:</b> <code>{esc(g['dial'] or cc)}</code>\n"
           f"{pe('box')} <b>Stock:</b> <b>{g['count']}</b> available\n"
           f"{pe('money')} <b>Lowest Price:</b> <code>{money(best_price)}</code></blockquote>\n\n"
           f"{pe('warn')} <b>Server 1</b> \u2022 Cheapest \u2022 <b>No guarantee</b> (sold as-is)\n"
           f"{pe('point')} <i>Tap a price below to buy that account.</i>")
    safe_edit(chat_id, mid, txt, kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("apicpg_"))
def cb_api_page(c):
    bot.answer_callback_query(c.id)
    try:
        page = int(c.data.split("_")[1])
    except Exception:
        page = 0
    _api_browse(c.message.chat.id, c.message.message_id, page)

@bot.callback_query_handler(func=lambda c: c.data.startswith("apic_"))
def cb_api_country(c):
    bot.answer_callback_query(c.id)
    parts = c.data[len("apic_"):].split("|")
    key = parts[0]
    page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    _api_country_view(c.message.chat.id, c.message.message_id, key, page)

@bot.callback_query_handler(func=lambda c: c.data == "apisort")
def cb_api_sort(c):
    bot.answer_callback_query(c.id)
    cur = _API_SORT.get(c.message.chat.id, "low")
    _API_SORT[c.message.chat.id] = "high" if cur == "low" else "low"
    _api_browse(c.message.chat.id, c.message.message_id, 0)

@bot.callback_query_handler(func=lambda c: c.data == "apitop10")
def cb_api_top10(c):
    bot.answer_callback_query(c.id)
    groups = _api_countries_grouped()
    rows = _api_grid_rows(groups, "low", "", 0.0)
    top = rows[:10]
    kb = types.InlineKeyboardMarkup(row_width=1)
    for i, g in enumerate(top):
        cc = g["cc"] or ""
        dial = g["dial"] or ""
        ptxt = money(g["price"]) if g["price"] > 0 else "\u2014"
        core = f"{cc}{dial}" if (cc or dial) else (g["name"] or "")[:10]
        label = f"{i+1}. {core} \u2022 {ptxt}"
        kb.add(IB(label, callback_data=f"apic_{g['key']}|0",
                  style=(BTN_SUCCESS if i < 3 else BTN_PRIMARY),
                  icon=flag_eid(cc) or EID("globe")))
    kb.add(IB("\u00ab Back", callback_data="browse_srv_1", style=BTN_PRIMARY, icon=EID("back")))
    kb.add(IB("MAIN MENU", callback_data="home", style=BTN_DANGER, icon=EID("home")))
    txt = (f"{pe('fire')} <b>Top 10 Lowest Price Countries</b>\n{DIV}\n"
           f"{pe('point')} <i>Cheapest accounts right now \u2014 tap to buy:</i>")
    safe_edit(c.message.chat.id, c.message.message_id, txt, kb)

@bot.callback_query_handler(func=lambda c: c.data == "apisearch")
def cb_api_search(c):
    bot.answer_callback_query(c.id)
    m = safe_send(c.message.chat.id,
                  f"{pe('search')} <b>Search Country</b>\n{DIV}\n"
                  f"{pe('point')} Send a country name or code (e.g. <code>India</code>, <code>US</code>).\n"
                  f"{pe('info')} Send <code>-</code> to clear the search.",
                  reply_markup=types.InlineKeyboardMarkup().add(
                      IB("CANCEL", callback_data="browse_srv_1", style=BTN_DANGER, icon=EID("cross"))))
    bot.register_next_step_handler(m, step_api_search)

def step_api_search(m):
    cid = m.chat.id
    t = (m.text or "").strip()
    if t in ("-", ""):
        _API_SEARCH.pop(cid, None)
    else:
        _API_SEARCH[cid] = t
    _api_browse(cid, None, 0)

@bot.callback_query_handler(func=lambda c: c.data == "apifilter")
def cb_api_filter(c):
    bot.answer_callback_query(c.id)
    m = safe_send(c.message.chat.id,
                  f"{pe('gear')} <b>Filter by Budget</b>\n{DIV}\n"
                  f"{pe('point')} Send your maximum budget in \u20b9 (e.g. <code>50</code>).\n"
                  f"{pe('info')} Send <code>-</code> to clear the filter.",
                  reply_markup=types.InlineKeyboardMarkup().add(
                      IB("CANCEL", callback_data="browse_srv_1", style=BTN_DANGER, icon=EID("cross"))))
    bot.register_next_step_handler(m, step_api_filter)

def step_api_filter(m):
    cid = m.chat.id
    t = (m.text or "").strip().replace("\u20b9", "").replace(",", "")
    if t in ("-", ""):
        _API_BUDGET.pop(cid, None)
    else:
        try:
            _API_BUDGET[cid] = float(t)
        except Exception:
            pass
    _api_browse(cid, None, 0)

@bot.callback_query_handler(func=lambda c: c.data.startswith("apibuy_") and not c.data.startswith("apibuygo_"))
def cb_api_buy(c):
    bot.answer_callback_query(c.id)
    country = c.data[len("apibuy_"):]
    c_info = _api_find_country(country)
    cc = (c_info.get("cc") or "").upper()
    price, _src = api_price_for(country, cc, c_info.get("min_price", 0))
    usr = get_user(c.from_user.id) or {}
    bal = float(usr.get("balance", 0))
    flag = pe_flag(cc)
    kb = types.InlineKeyboardMarkup()
    kb.add(IB("CONFIRM PURCHASE", callback_data=f"apibuygo_{country}", style=BTN_SUCCESS, icon=EID("check")),
           IB("CANCEL", callback_data=f"apic_{cc}|0", style=BTN_DANGER, icon=EID("cross")))
    txt = (f"{pe('cart')} <b>Confirm Purchase</b>\n{DIV}\n"
           f"{flag} <b>{esc(c_info.get('name') or country)}</b> \u2022 <code>{esc(c_info.get('dial') or '')}</code>\n"
           f"{SERVER_EMOJI[1]} <b>Server:</b> Server 1 \u2022 No guarantee\n"
           f"{pe('money')} Price: <code>{money(price)}</code>\n"
           f"{pe('wallet')} Your Balance: <code>{money(bal)}</code>\n{DIV}\n"
           f"{pe('warn')} <b>No guarantee</b> \u2014 sold as-is.\n"
           f"{pe('shield')} <i>After purchase you'll receive the phone number &amp; login code here.</i>")
    safe_edit(c.message.chat.id, c.message.message_id, txt, kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("apibuygo_"))
def cb_api_buy_go(c):
    country = c.data[len("apibuygo_"):]
    c_info = _api_find_country(country)
    cc = (c_info.get("cc") or "").upper()
    price, _src = api_price_for(country, cc, c_info.get("min_price", 0))
    usr = get_user(c.from_user.id) or {}
    bal = float(usr.get("balance", 0))
    if price <= 0:
        bot.answer_callback_query(c.id, "Price not set.", show_alert=True)
        return
    if bal < price:
        bot.answer_callback_query(c.id, "Insufficient balance!", show_alert=True)
        need = price - bal
        safe_edit(c.message.chat.id, c.message.message_id,
                  f"{pe('cross')} <b>Insufficient Balance</b>\n{DIV}\n"
                  f"{pe('wallet')} Your Balance: <code>{money(bal)}</code>\n"
                  f"{pe('money')} Account Price: <code>{money(price)}</code>\n"
                  f"{pe('bolt')} You need <b>{money(need)}</b> more.\n{DIV}\n"
                  f"{pe('point')} Please add balance to continue.",
                  reply_markup=types.InlineKeyboardMarkup().add(
                      IB("ADD BALANCE", callback_data="addbal", style=BTN_SUCCESS, icon=EID("card")),
                      IB("MAIN MENU", callback_data="home", style=BTN_DANGER, icon=EID("home"))))
        return
    bot.answer_callback_query(c.id, "Purchasing\u2026")
    safe_edit(c.message.chat.id, c.message.message_id,
              f"{pe('clock')} <b>Purchasing your account\u2026</b>\n{DIV}\n"
              f"{pe('info')} Please wait a moment while we prepare your account.")
    _api_purchase(c.message.chat.id, c.from_user.id, country, c_info, price)

def _api_purchase(chat_id, uid, country, c_info, price):
    """Execute a Server-1 purchase: get number -> deduct -> order -> relay code."""
    r = tgshark_buy(country)
    if str(r.get("status")) != "ok":
        msg = r.get("message") or "Purchase failed"
        cc = (c_info.get("cc") or "").upper()
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(IB("RETRY", callback_data=f"apibuygo_{country}", style=BTN_SUCCESS, icon=EID("refresh")))
        kb.add(IB("BACK", callback_data=f"apic_{cc}|0", style=BTN_PRIMARY, icon=EID("back")))
        kb.add(IB("MAIN MENU", callback_data="home", style=BTN_DANGER, icon=EID("home")))
        safe_send(chat_id, f"{pe('cross')} <b>Could not buy account</b>\n{DIV}\n"
                           f"{pe('info')} <code>{esc(msg)}</code>\n"
                           f"{pe('point')} No balance was deducted.", reply_markup=kb)
        return
    phone = str(r.get("phone") or r.get("number") or "")
    hash_code = str(r.get("hash_code") or r.get("hash") or r.get("hashCode") or "")
    twofa = r.get("twofa") or r.get("twoFA") or ""
    if not phone or not hash_code:
        safe_send(chat_id, f"{pe('cross')} <b>We couldn't complete your order.</b>\n{DIV}\n"
                           f"{pe('info')} Please try again or contact support.\n"
                           f"{pe('point')} No balance was deducted.")
        return
    cc = (c_info.get("cc") or "").upper()
    dial = c_info.get("dial") or ""
    name = c_info.get("name") or country
    now = datetime.utcnow().isoformat()
    with _db_lock:
        con = db(); cur = con.cursor()
        cur.execute("""INSERT INTO accounts(phone,api_id,api_hash,session,country,cc,code,price,
                       status,added_by,added_at,label,server,platform,seller_id,sold_to,sold_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (phone, "", "", "", name, cc, dial, price, "sold", OWNER_ID, now,
                     "Server 1 Account", 1, "telegram", 0, uid, now))
        aid = cur.lastrowid
        cur.execute("""INSERT INTO orders(user_id,account_id,price,status,created_at,updated_at,
                       api_hash,api_phone,api_country,is_api)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (uid, aid, price, "awaiting_login", now, now, hash_code, phone, country, 1))
        order_id = cur.lastrowid
        cur.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (price, uid))
        con.commit(); con.close()
    acc = {"id": aid, "phone": phone, "country": name, "cc": cc, "code": dial,
           "price": price, "server": 1, "label": "Server 1 Account", "platform": "telegram",
           "session": ""}
    flag = pe_flag(cc)
    txt = (f"{pe('check')} <b>Purchase Successful!</b>\n{DIV}\n"
           f"{flag} <b>{esc(name)}</b> \u2022 <code>{esc(dial)}</code>\n"
           f"{SERVER_EMOJI[1]} <b>Server:</b> Server 1 \u2022 No guarantee\n"
           f"{pe('phone')} <b>Phone Number:</b> <code>{esc(phone)}</code>\n"
           f"{pe('box')} <b>Order ID:</b> <code>#{order_id}</code>\n{DIV}\n"
           f"{pe('bolt')} <b>How to login:</b>\n"
           f"{pe('point')} <b>1.</b> Login with the phone number above in Telegram.\n"
           f"{pe('point')} <b>2.</b> Telegram will send a login code \u2014 we'll deliver it here automatically.\n"
           f"{pe('point')} <b>3.</b> Enter the code to finish login.\n{DIV}\n"
           f"{pe('clock')} <i>Waiting for the login code\u2026 keep this chat open.</i>")
    safe_send(chat_id, txt, reply_markup=types.InlineKeyboardMarkup().add(
        IB("MY ORDERS", callback_data="orders", style=BTN_PRIMARY, icon=EID("box")),
        IB("MAIN MENU", callback_data="home", style=BTN_DANGER, icon=EID("home"))))
    _post_purchase_proof(order_id, acc, type("U", (), {"id": uid, "username": None})())
    start_api_otp_relay(order_id, hash_code, acc, uid)

def start_api_otp_relay(order_id, hash_code, acc, user_id):
    evt = threading.Event()
    _OTP_STOP[order_id] = evt
    t = threading.Thread(target=_api_otp_thread, args=(order_id, hash_code, acc, user_id, evt), daemon=True)
    t.start()

def _api_otp_thread(order_id, hash_code, acc, user_id, stop_evt):
    delivered = None
    waited = 0
    while not stop_evt.is_set() and waited < 1200:   # up to ~20 min
        r = tgshark_code(hash_code)
        if str(r.get("status")) == "ok":
            code = r.get("code") or r.get("sms") or ""
            if code:
                delivered = str(code)
                break
        time.sleep(6); waited += 6
    if stop_evt.is_set():
        return
    flag = pe_flag(acc.get("cc"))
    if not delivered:
        safe_send(user_id,
                  f"{pe('clock')} <b>Still waiting for the code\u2026</b>\n{DIV}\n"
                  f"{flag} <b>{esc(acc.get('country'))}</b> \u2022 <code>{esc(acc.get('code'))}</code>\n"
                  f"{pe('info')} The login code hasn't arrived yet. Please try logging in again \u2014 "
                  f"if it still doesn't arrive, contact support with your Order ID.\n"
                  f"{pe('box')} Order ID: <code>#{order_id}</code>",
                  reply_markup=types.InlineKeyboardMarkup().add(
                      IB("SUPPORT", url=SUPPORT_URL, style=BTN_PRIMARY, icon=EID("shield")),
                      IB("MY ORDERS", callback_data="orders", style=BTN_PRIMARY, icon=EID("box"))))
        return
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("UPDATE orders SET otp=?, updated_at=? WHERE id=?",
                  (delivered, datetime.utcnow().isoformat(), order_id))
        con.commit(); con.close()
    txt = (f"{pe('check')} <b>Login Code Received!</b>\n{DIV}\n"
           f"{flag} <b>{esc(acc.get('country'))}</b> \u2022 <code>{esc(acc.get('code'))}</code>\n"
           f"{pe('lock')} <b>Your OTP:</b> <code>{delivered}</code>\n{DIV}\n"
           f"{pe('bolt')} Enter this code in Telegram now.\n"
           f"{pe('point')} After you log in, confirm below.")
    kb = types.InlineKeyboardMarkup()
    kb.add(IB("YES, I HAVE LOGGED IN", callback_data=f"logy_{order_id}", style=BTN_SUCCESS, icon=EID("check")),
           IB("NO, I HAVE NOT LOGGED IN", callback_data=f"logn_{order_id}", style=BTN_DANGER, icon=EID("cross")))
    safe_send(user_id, txt, reply_markup=kb)

def _platform_chooser(chat_id, mid=None):
    kb = types.InlineKeyboardMarkup(row_width=1)
    tg = _count("SELECT COUNT(*) n FROM accounts WHERE status='available' AND COALESCE(platform,'telegram')='telegram'")
    wa = _count("SELECT COUNT(*) n FROM accounts WHERE status='available' AND COALESCE(platform,'telegram')='whatsapp'")
    kb.add(IB("\u2708\ufe0f Telegram Accounts  \u2022  %d in stock" % tg, callback_data="browse_tg_0", style=BTN_SUCCESS, icon=EID("send")))
    kb.add(IB("\U0001F4AC WhatsApp Accounts  \u2022  %d in stock" % wa, callback_data="browse_wa_0", style=BTN_SUCCESS, icon=EID("phone")))
    kb.add(IB("MAIN MENU", callback_data="home", style=BTN_DANGER, icon=EID("home")))
    txt = (f"{pe('cart')} <b>Browse Accounts</b>\n{DIV}\n"
           f"{pe('point')} Choose a platform to browse:\n"
           f"{pe('info')} \u2708\ufe0f <b>Telegram</b> and \U0001F4AC <b>WhatsApp</b> accounts are listed separately.")
    if mid:
        safe_edit(chat_id, mid, txt, kb)
    else:
        safe_send(chat_id, txt, reply_markup=kb)

def browse_kb(page=0, per_page=8, platform="telegram"):
    rows = country_rows(platform)
    kb = types.InlineKeyboardMarkup(row_width=1)
    start = page * per_page
    chunk = rows[start:start + per_page]
    for _i, r in enumerate(chunk):
        cc = (r["cc"] or "").upper()
        label = f"{r['cc'] or ''} {r['code'] or ''} \u2022 {money(r['minp'])} \u2022 {r['n']} in stock"
        kb.add(IB(label, callback_data=f"ctry_{platform}|{r['country']}",
                  style=_acct_style(_i), icon=FLAGS.get(cc) or EID("globe")))
    nav = []
    tag = "tg" if platform == "telegram" else "wa"
    if page > 0:
        nav.append(IB("PREV", callback_data=f"browse_{tag}_{page-1}", style=BTN_PRIMARY, icon=EID("back")))
    if start + per_page < len(rows):
        nav.append(IB("NEXT", callback_data=f"browse_{tag}_{page+1}", style=BTN_PRIMARY, icon=EID("arrow")))
    if nav:
        kb.row(*nav)
    kb.add(IB("FILTER BY BUDGET", callback_data=f"filter_{platform}", style=BTN_SUCCESS, icon=EID("search")))
    kb.add(IB("SWITCH PLATFORM", callback_data="browse", style=BTN_PRIMARY, icon=EID("refresh")),
           IB("MAIN MENU", callback_data="home", style=BTN_DANGER, icon=EID("home")))
    return kb

@bot.callback_query_handler(func=lambda c: c.data == "browse" or c.data.startswith("browse_"))
def cb_browse(c):
    bot.answer_callback_query(c.id)
    if c.data == "browse":
        _browse_server_chooser(c.message.chat.id, c.message.message_id)
        return
    if c.data == "browse_srv_1":
        _api_browse(c.message.chat.id, c.message.message_id, 0)
        return
    if c.data == "browse_srv_2":
        _platform_chooser(c.message.chat.id, c.message.message_id)
        return
    parts = c.data.split("_")   # browse_<tag>_<page>
    tag = parts[1] if len(parts) > 1 else "tg"
    platform = "whatsapp" if tag == "wa" else "telegram"
    try:
        page = int(parts[2]) if len(parts) > 2 else 0
    except Exception:
        page = 0
    if not country_rows(platform):
        safe_edit(c.message.chat.id, c.message.message_id,
                  f"{pe('cross')} <b>No {PLATFORM_LABEL[platform]} accounts in stock right now.</b>\n"
                  f"{pe('clock')} Please check back later.",
                  reply_markup=types.InlineKeyboardMarkup().add(
                      IB("SWITCH PLATFORM", callback_data="browse", style=BTN_PRIMARY, icon=EID("refresh")),
                      IB("MAIN MENU", callback_data="home", style=BTN_DANGER, icon=EID("home"))))
        return
    safe_edit(c.message.chat.id, c.message.message_id,
              browse_text(c.from_user, platform), browse_kb(page, 8, platform))

@bot.callback_query_handler(func=lambda c: c.data == "filter" or c.data.startswith("filter_"))
def cb_filter(c):
    bot.answer_callback_query(c.id)
    platform = "telegram"
    if c.data.startswith("filter_"):
        platform = c.data.split("_", 1)[1] or "telegram"
    msg = safe_send(c.message.chat.id,
                    f"{pe('search')} <b>Filter by Budget</b> \u2022 {PLATFORM_LABEL.get(platform,'')}\n{DIV}\n"
                    f"{pe('point')} Send your <b>maximum budget</b> in {CURRENCY} "
                    f"(e.g. <code>20</code>).\n"
                    f"{pe('info')} Only {PLATFORM_LABEL.get(platform,'')} accounts within your budget will be shown.")
    bot.register_next_step_handler(msg, lambda m: step_filter_budget(m, platform))

def step_filter_budget(m, platform="telegram"):
    try:
        budget = float(re.sub(r'[^\d.]', '', m.text or ""))
    except Exception:
        return safe_send(m.chat.id, f"{pe('cross')} Invalid amount. Please send a number.")
    if budget <= 0:
        return safe_send(m.chat.id, f"{pe('cross')} Enter a positive number.")
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("""SELECT * FROM accounts WHERE status='available' AND price<=?
                     AND COALESCE(platform,'telegram')=?
                     ORDER BY price ASC, id ASC LIMIT 30""", (budget, platform))
        rows = [dict(r) for r in c.fetchall()]; con.close()
    kb = types.InlineKeyboardMarkup(row_width=1)
    if not rows:
        txt = (f"{pe('search')} <b>Budget:</b> <code>{money(budget)}</code>\n{DIV}\n"
               f"{pe('cross')} No {PLATFORM_LABEL.get(platform,'')} accounts found within your budget right now.\n"
               f"{pe('info')} Try a higher budget or check back later.")
    else:
        txt = (f"{pe('search')} <b>Accounts within {money(budget)}</b>\n{DIV}\n"
               f"{pe('box')} Found <b>{len(rows)}</b> matching account(s).\n"
               f"{pe('point')} Tap an account below to purchase.")
        for _i, r in enumerate(rows):
            cc = (r["cc"] or "").upper()
            lbl = (r.get("label") or "").strip()
            t = f"{_srv_badge(r.get('server'))} {r['cc'] or ''} {r['code'] or ''} \u2022 {money(r['price'])}"
            if lbl:
                t += f" \u2022 {lbl}"
            kb.add(IB(t, callback_data=f"buy_{r['id']}",
                      style=_acct_style(_i), icon=FLAGS.get(cc) or EID("globe")))
    kb.add(IB("FILTER AGAIN", callback_data=f"filter_{platform}", style=BTN_SUCCESS, icon=EID("search")),
           IB("BACK", callback_data="browse", style=BTN_PRIMARY, icon=EID("back")))
    kb.add(IB("MAIN MENU", callback_data="home", style=BTN_DANGER, icon=EID("home")))
    safe_send(m.chat.id, txt, reply_markup=kb)

def accounts_in_country(country, platform="telegram"):
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("""SELECT * FROM accounts WHERE status='available' AND country=?
                     AND COALESCE(platform,'telegram')=?
                     ORDER BY price ASC, id ASC""", (country, platform))
        rows = [dict(r) for r in c.fetchall()]; con.close()
        return rows

def country_text(country, page=0, per_page=8, platform="telegram"):
    rows = accounts_in_country(country, platform)
    if not rows:
        return f"{pe('cross')} <b>No accounts available for {esc(country)}.</b>", None, 0
    cc = rows[0]["cc"]; code = rows[0]["code"]
    flag = pe_flag(cc)
    minp = min(r["price"] for r in rows)
    total_pages = max(1, (len(rows) + per_page - 1) // per_page)
    s1 = sum(1 for r in rows if int(r.get("server") or 1) == 1)
    s2 = sum(1 for r in rows if int(r.get("server") or 1) == 2)
    if s1 and s2:
        srv_line = (f"{SERVER_EMOJI[1]} Server 1: <b>{s1}</b>  \u2022  "
                    f"{SERVER_EMOJI[2]} Server 2: <b>{s2}</b>\n")
    elif s2:
        srv_line = f"{SERVER_EMOJI[2]} <b>Server 2</b> (Guaranteed)\n"
    else:
        srv_line = f"{SERVER_EMOJI[1]} <b>Server 1</b> (No guarantee)\n"
    return (
        f"{flag} <b>{esc(country)}</b> \u2022 {PLATFORM_EMOJI.get(platform,'')} {PLATFORM_LABEL.get(platform,'')}\n"
        f"{pe('phone')} <b>Country Code:</b> <code>{esc(code)}</code>\n"
        f"{DIV}\n"
        f"<blockquote>{pe('box')} <b>Available Stock:</b> <b>{len(rows)}</b> accounts\n"
        f"{srv_line}"
        f"{pe('money')} <b>Starting From:</b> <code>{money(minp)}</code>\n"
        f"{pe('chart')} <b>Page:</b> {page+1}/{total_pages}</blockquote>\n"
        f"\n"
        f"{pe('point')} <i>Tap an account below to purchase.</i>"
    ), rows, total_pages

@bot.callback_query_handler(func=lambda c: c.data.startswith("ctry_"))
def cb_country(c):
    bot.answer_callback_query(c.id)
    parts = c.data[len("ctry_"):].split("|")
    platform = "telegram"
    if len(parts) >= 2 and parts[0] in ("telegram", "whatsapp"):
        platform = parts[0]
        country = parts[1]
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    else:
        country = parts[0]
        page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    txt, rows, total_pages = country_text(country, page, 8, platform)
    kb = types.InlineKeyboardMarkup(row_width=1)
    if rows:
        per_page = 8
        chunk = rows[page*per_page:(page+1)*per_page]
        for _i, r in enumerate(chunk):
            cc = (r["cc"] or "").upper()
            lbl = (r.get("label") or "").strip()
            t = f"{_srv_badge(r.get('server'))} {r['cc'] or ''} {r['code'] or ''} \u2022 {money(r['price'])}"
            if lbl:
                t += f" \u2022 {lbl}"
            kb.add(IB(t, callback_data=f"buy_{r['id']}",
                      style=_acct_style(_i), icon=FLAGS.get(cc) or EID("globe")))
        nav = []
        if page > 0:
            nav.append(IB("PREV", callback_data=f"ctry_{platform}|{country}|{page-1}", style=BTN_PRIMARY, icon=EID("back")))
        if page + 1 < total_pages:
            nav.append(IB("NEXT", callback_data=f"ctry_{platform}|{country}|{page+1}", style=BTN_PRIMARY, icon=EID("arrow")))
        if nav:
            kb.row(*nav)
        kb.add(IB("ACCEPT TERMS & POLICIES", callback_data="terms", style=BTN_SUCCESS, icon=EID("doc")))
    kb.add(IB("BACK", callback_data="browse", style=BTN_PRIMARY, icon=EID("back")),
           IB("MAIN MENU", callback_data="home", style=BTN_DANGER, icon=EID("home")))
    safe_edit(c.message.chat.id, c.message.message_id, txt, kb)

# ═══════════════════════════════════════════════════════════════════════════
#  BUY FLOW
# ═══════════════════════════════════════════════════════════════════════════
def get_account(aid):
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("SELECT * FROM accounts WHERE id=?", (aid,))
        r = c.fetchone(); con.close()
        return dict(r) if r else None

@bot.callback_query_handler(func=lambda c: c.data.startswith("buy_"))
def cb_buy(c):
    aid = int(c.data.split("_")[1])
    acc = get_account(aid)
    if not acc or acc["status"] != "available":
        bot.answer_callback_query(c.id, "This account is no longer available.", show_alert=True)
        return
    usr = get_user(c.from_user.id) or {}
    bal = float(usr.get("balance", 0))
    price = float(acc["price"])
    if bal < price:
        bot.answer_callback_query(c.id, "Insufficient balance!", show_alert=True)
        need = price - bal
        safe_edit(c.message.chat.id, c.message.message_id,
                  f"{pe('cross')} <b>Insufficient Balance</b>\n"
                  f"{DIV}\n"
                  f"{pe('wallet')} Your Balance: <code>{money(bal)}</code>\n"
                  f"{pe('money')} Account Price: <code>{money(price)}</code>\n"
                  f"{pe('bolt')} You need <b>{money(need)}</b> more.\n"
                  f"{DIV}\n"
                  f"{pe('point')} Please add balance to continue.",
                  reply_markup=types.InlineKeyboardMarkup().add(
                      IB("💳 Add Balance", callback_data="addbal"),
                      IB("🏠 Main Menu", callback_data="home")))
        return
    flag = pe_flag(acc["cc"])
    kb = types.InlineKeyboardMarkup()
    kb.add(IB("✅ Confirm Purchase", callback_data=f"dobuy_{aid}"),
           IB("❌ Cancel", callback_data=f"ctry_{acc['country']}"))
    safe_edit(c.message.chat.id, c.message.message_id,
              f"{pe('cart')} <b>Confirm Purchase</b>\n"
              f"{DIV}\n"
              f"{flag} <b>{esc(acc['country'])}</b> • <code>{esc(acc['code'])}</code>\n"
              f"{pe('tag')} <b>Label:</b> {esc(acc.get('label') or 'Standard')}\n"
              f"{_srv_badge(acc.get('server'))} <b>Server:</b> {SERVER_LABELS.get(int(acc.get('server') or 1), 'Server 1')}\n"
              f"{pe('money')} Price: <code>{money(price)}</code>\n"
              f"{pe('wallet')} Your Balance: <code>{money(bal)}</code>\n"
              f"{DIV}\n"
              f"{_srv_disclaimer(acc.get('server'))}\n"
              f"{pe('shield')} <i>After purchase you'll receive the phone number &amp; login steps.</i>",
              kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("dobuy_"))
def cb_dobuy(c):
    aid = int(c.data.split("_")[1])
    acc = get_account(aid)
    if not acc or acc["status"] != "available":
        bot.answer_callback_query(c.id, "Already sold.", show_alert=True)
        return
    usr = get_user(c.from_user.id) or {}
    bal = float(usr.get("balance", 0)); price = float(acc["price"])
    if bal < price:
        bot.answer_callback_query(c.id, "Insufficient balance.", show_alert=True)
        return
    with _db_lock:
        con = db(); c2 = con.cursor()
        c2.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (price, c.from_user.id))
        c2.execute("UPDATE accounts SET status='sold', sold_to=?, sold_at=? WHERE id=?",
                   (c.from_user.id, datetime.utcnow().isoformat(), aid))
        c2.execute("""INSERT INTO orders(user_id,account_id,price,status,created_at,updated_at)
                      VALUES(?,?,?,?,?,?)""",
                   (c.from_user.id, aid, price, "awaiting_login",
                    datetime.utcnow().isoformat(), datetime.utcnow().isoformat()))
        order_id = c2.lastrowid
        con.commit(); con.close()
    bot.answer_callback_query(c.id, "Purchased!")
    flag = pe_flag(acc["cc"])
    txt = (
        f"{pe('check')} <b>Purchase Successful!</b>\n"
        f"{DIV}\n"
        f"{flag} <b>{esc(acc['country'])}</b> • <code>{esc(acc['code'])}</code>\n"
        f"{pe('tag')} <b>Label:</b> {esc(acc.get('label') or 'Standard')}\n"
        f"{_srv_badge(acc.get('server'))} <b>Server:</b> {SERVER_LABELS.get(int(acc.get('server') or 1), 'Server 1')}\n"
        f"{pe('phone')} <b>Phone Number:</b> <code>{esc(acc['phone'])}</code>\n"
        f"{pe('box')} <b>Order ID:</b> <code>#{order_id}</code>\n"
        f"{DIV}\n"
        f"{pe('bolt')} <b>How to login:</b>\n"
        f"{pe('point')} <b>1.</b> Try to login on <b>Telegram X</b> or <b>Nicegram</b> for a better experience.\n"
        f"{pe('point')} <b>2.</b> Login with the phone number above.\n"
        f"{pe('point')} <b>3.</b> Telegram will send a login code — <b>send it here</b> and we'll deliver it to you.\n"
        f"{DIV}\n"
        f"{pe('clock')} <i>Waiting for the login code… keep this chat open.</i>"
    )
    safe_edit(c.message.chat.id, c.message.message_id, txt,
              types.InlineKeyboardMarkup().add(
                  IB("📦 My Orders", callback_data="orders"),
                  IB("🏠 Main Menu", callback_data="home")))
    _post_purchase_proof(order_id, acc, c.from_user)
    start_otp_relay(order_id, acc, c.from_user.id)

# ═══════════════════════════════════════════════════════════════════════════
#  OTP RELAY + LOGIN DETECTION  (Telegram service 777000 on the account)
# ═══════════════════════════════════════════════════════════════════════════
_OTP_STOP = {}    # order_id -> threading.Event
_NEW_AUTHS = {}   # order_id -> set of newly-detected authorization hashes (buyer logins)

def start_otp_relay(order_id, acc, user_id):
    if not TELETHON_OK or not acc.get("session"):
        return
    evt = threading.Event()
    _OTP_STOP[order_id] = evt
    t = threading.Thread(target=_otp_relay_thread, args=(order_id, acc, user_id, evt), daemon=True)
    t.start()

def _otp_relay_thread(order_id, acc, user_id, stop_evt):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_otp_relay_async(order_id, acc, user_id, stop_evt))
    except Exception as e:
        log.error("otp relay error: %s", e)
    finally:
        try:
            loop.close()
        except Exception:
            pass

async def _otp_relay_async(order_id, acc, user_id, stop_evt):
    client = _tl_client(acc["session"], acc["api_id"], acc["api_hash"])
    try:
        await client.connect()
    except Exception as e:
        log.error("otp relay connect failed: %s", e)
        return
    delivered = {"code": None, "detected": False}

    # baseline active authorizations (our own session)
    base_hashes = set()
    try:
        auths = await client(functions.account.GetAuthorizationsRequest())
        base_hashes = {a.hash for a in auths.authorizations}
    except Exception:
        pass

    @client.on(events.NewMessage(from_users=777000))
    async def handler(event):
        text = event.raw_text or ""
        m = re.search(r'\b(\d{4,7})\b', text)
        if not m:
            return
        code = m.group(1)
        if delivered["code"] == code:
            return
        delivered["code"] = code
        with _db_lock:
            con = db(); c = con.cursor()
            c.execute("UPDATE orders SET otp=?, updated_at=? WHERE id=?",
                      (code, datetime.utcnow().isoformat(), order_id))
            con.commit(); con.close()
        flag = pe_flag(acc.get("cc"))
        txt = (f"{pe('check')} <b>Login Code Received!</b>\n"
               f"{DIV}\n"
               f"{flag} <b>{esc(acc.get('country'))}</b> • <code>{esc(acc.get('code'))}</code>\n"
               f"{pe('lock')} <b>Your OTP:</b> <code>{code}</code>\n"
               f"{DIV}\n"
               f"{pe('bolt')} Enter this code in Telegram now.\n"
               f"{pe('point')} After you log in, confirm below.")
        kb = types.InlineKeyboardMarkup()
        kb.add(IB("✅ Yes, I have logged in", callback_data=f"logy_{order_id}"),
               IB("❌ No, I have not logged in", callback_data=f"logn_{order_id}"))
        safe_send(user_id, txt, reply_markup=kb)

    # poll for a new authorization (buyer's login) for up to 20 min
    waited = 0
    while not stop_evt.is_set() and waited < 1200:
        await asyncio.sleep(2); waited += 2
        if not delivered["detected"]:
            try:
                auths = await client(functions.account.GetAuthorizationsRequest())
                cur = {a.hash for a in auths.authorizations}
                new_hashes = cur - base_hashes
                if len(new_hashes) > 0:
                    delivered["detected"] = True
                    _NEW_AUTHS[order_id] = set(new_hashes)
                    flag = pe_flag(acc.get("cc"))
                    txt = (f"{pe('bell')} <b>New Login Detected</b>\n"
                           f"{DIV}\n"
                           f"{flag} <b>{esc(acc.get('country'))}</b> • <code>{esc(acc.get('code'))}</code>\n"
                           f"{pe('eyes')} <i>We have detected a proper new login — we think you have logged in.</i>\n"
                           f"{DIV}\n"
                           f"{pe('point')} Please confirm below:")
                    kb = types.InlineKeyboardMarkup()
                    kb.add(IB("✅ Yes, I have logged in", callback_data=f"logy_{order_id}"),
                           IB("❌ No, I have not logged in", callback_data=f"logn_{order_id}"))
                    safe_send(user_id, txt, reply_markup=kb)
            except Exception:
                pass
    try:
        await client.disconnect()
    except Exception:
        pass

@bot.callback_query_handler(func=lambda c: c.data.startswith("logy_"))
def cb_log_yes(c):
    oid = int(c.data.split("_")[1])
    bot.answer_callback_query(c.id, "Confirmed — logging out from our side…")
    _finalize_order(oid, True)
    safe_edit(c.message.chat.id, c.message.message_id,
              f"{pe('check')} <b>Thank you!</b>\n"
              f"{DIV}\n"
              f"{pe('shield')} Your account has been fully transferred.\n"
              f"{pe('gem')} Enjoy! Need help? Contact support.",
              types.InlineKeyboardMarkup().add(
                  IB("🏠 Main Menu", callback_data="home")))

@bot.callback_query_handler(func=lambda c: c.data.startswith("logn_"))
def cb_log_no(c):
    oid = int(c.data.split("_")[1])
    bot.answer_callback_query(c.id, "New login removed.")
    _finalize_order(oid, False)
    safe_edit(c.message.chat.id, c.message.message_id,
              f"{pe('cross')} <b>New Login Removed</b>\n"
              f"{DIV}\n"
              f"{pe('shield')} The newly detected login session has been removed immediately.\n"
              f"{pe('bolt')} Please try logging in again or contact support.",
              types.InlineKeyboardMarkup().add(
                  IB("🛡 Support", url=SUPPORT_URL),
                  IB("🏠 Main Menu", callback_data="home")))

def _finalize_order(order_id, logged_in):
    evt = _OTP_STOP.pop(order_id, None)
    if evt:
        evt.set()
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("SELECT * FROM orders WHERE id=?", (order_id,))
        o = c.fetchone()
        if not o:
            con.close(); return
        o = dict(o)
        c.execute("UPDATE orders SET status=?, login_confirmed=?, updated_at=? WHERE id=?",
                  ("completed" if logged_in else "cancelled", 1 if logged_in else 0,
                   datetime.utcnow().isoformat(), order_id))
        con.commit(); con.close()
    new_hashes = _NEW_AUTHS.pop(order_id, set())
    if logged_in:
        # Buyer logged in successfully -> release OUR master session (full transfer).
        _logout_session(o["account_id"])
    else:
        # Buyer did NOT log in -> remove the NEWLY detected login(s) only,
        # keeping our master session alive so the buyer can try again.
        _kill_new_sessions(o["account_id"], new_hashes)

def _kill_new_sessions(account_id, hashes):
    """Terminate only the given (buyer) authorizations, keep the master session."""
    if not TELETHON_OK or not hashes:
        return
    acc = get_account(account_id)
    if not acc or not acc.get("session"):
        return
    def _do():
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        async def _inner():
            cl = _tl_client(acc["session"], acc["api_id"], acc["api_hash"])
            try:
                await cl.connect()
                for h in hashes:
                    try:
                        await cl(functions.account.ResetAuthorizationRequest(hash=h))
                    except Exception as e:
                        log.warning("reset auth %s failed: %s", h, e)
            except Exception as e:
                log.warning("kill_new_sessions failed: %s", e)
            finally:
                try: await cl.disconnect()
                except Exception: pass
        try:
            loop.run_until_complete(_inner())
        finally:
            loop.close()
    threading.Thread(target=_do, daemon=True).start()

def _logout_session(account_id):
    if not TELETHON_OK:
        return
    acc = get_account(account_id)
    if not acc or not acc.get("session"):
        return
    def _do():
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        async def _inner():
            cl = _tl_client(acc["session"], acc["api_id"], acc["api_hash"])
            try:
                await cl.connect()
                await cl.log_out()
            except Exception as e:
                log.warning("logout failed: %s", e)
            finally:
                try: await cl.disconnect()
                except Exception: pass
        try:
            loop.run_until_complete(_inner())
        finally:
            loop.close()
    threading.Thread(target=_do, daemon=True).start()

# ═══════════════════════════════════════════════════════════════════════════
def _post_purchase_proof(order_id, acc, user):
    """Post a premium 'NEW PURCHASE COMPLETE' audit log.

    Telegram does not let a bot use premium (custom) emoji directly inside a
    channel, so we first send the premium message (with an inline button) to the
    OWNER's DM as a reference, then forward that exact message to the proof
    channel the admin selected in the panel."""
    try:
        if str(get_setting("proof_enabled", "1")) == "0":
            return
        uid = getattr(user, "id", None)
        uname = ("@" + user.username) if getattr(user, "username", None) else "\u2014"
        srv = int(acc.get("server") or 1)
        srv_name = SERVER_LABELS.get(srv, "Server 1")
        srv_emoji = SERVER_EMOJI.get(srv, "\U0001F7E2")
        flag = pe_flag(acc.get("cc"))
        botname = "@" + (BOT_USERNAME or get_setting("bot_username", "") or "Free_tg_ids_bot")
        now = datetime.utcnow().strftime("%d %b %Y \u2022 %H:%M UTC")
        txt = (
            f"{pe('party')} <b>NEW PURCHASE COMPLETE</b> {pe('party')}\n"
            f"{DIV}\n"
            f"<blockquote>"
            f"{pe('user')} <b>User ID:</b> <code>{uid}</code>\n"
            f"{pe('user')} <b>Username:</b> {esc(uname)}\n"
            f"{pe('globe')} <b>Country:</b> {flag} {esc(acc.get('country') or '-')} "
            f"<code>{esc(acc.get('code') or '')}</code>\n"
            f"{srv_emoji} <b>Server:</b> {srv_name}\n"
            f"{pe('money')} <b>Price:</b> <code>{money(acc.get('price'))}</code>\n"
            f"{pe('box')} <b>Order:</b> <code>#{order_id}</code>\n"
            f"{pe('clock')} <b>Time:</b> {now}\n"
            f"{pe('rocket')} <b>Powered by</b> {esc(botname)}"
            f"</blockquote>"
        )
        kb = types.InlineKeyboardMarkup().add(
            IB("\U0001F6D2 Get Your Own Account",
               url=f"https://t.me/{BOT_USERNAME or 'Free_tg_ids_bot'}",
               style=BTN_SUCCESS, icon=EID("cart")))
        # 1) premium reference message -> owner DM (premium emoji + inline button)
        ref = safe_send(OWNER_ID, txt, reply_markup=kb)
        # 2) forward that exact message to the selected proof channel
        chan = (get_setting("proof_channel", "") or "").strip()
        if chan and ref:
            method = None
            try:
                bot.forward_message(chan, OWNER_ID, ref.message_id)
                method = "forward"
            except Exception as e:
                log.warning("proof forward failed (%s): %s", chan, e)
            if method is None:
                # fallback: copy the premium message and attach the button
                try:
                    bot.copy_message(chan, OWNER_ID, ref.message_id, reply_markup=kb)
                    method = "copy"
                except Exception as e:
                    log.warning("proof copy failed (%s): %s", chan, e)
            if method is None:
                # last resort: post the text directly
                try:
                    bot.send_message(chan, txt, reply_markup=kb,
                                     disable_web_page_preview=True)
                    method = "direct"
                except Exception as e:
                    log.warning("proof direct post failed (%s): %s", chan, e)
            # a plain forward drops the inline keyboard -> attach the CTA button
            if method == "forward":
                try:
                    bot.send_message(chan, "Get your own account \U0001F447",
                                     reply_markup=kb, disable_web_page_preview=True)
                except Exception as e:
                    log.warning("proof button post failed (%s): %s", chan, e)
    except Exception as e:
        log.warning("post_purchase_proof failed: %s", e)


#  WALLET / ADD BALANCE  (UPI QR + auto/manual verification)
# ═══════════════════════════════════════════════════════════════════════════
_PENDING_DEPOSIT = {}   # user_id -> amount

def wallet_text(u):
    usr = get_user(u.id) or {}
    bal = money(usr.get("balance", 0))
    min_dep = get_setting("min_deposit", "20")
    return (
        f"{pe('star')} <b>{esc(STORE_NAME)}</b> \u2022 {pe('card')} <b>Deposit Funds</b>\n"
        f"{DIV}\n"
        f"<blockquote>{pe('wallet')} <b>Your Wallet Balance:</b> <code>{bal}</code>\n"
        f"{pe('money')} <b>Minimum Deposit:</b> <code>{CURRENCY}{esc(min_dep)}</code>\n"
        f"{pe('bolt')} <b>Verification:</b> Automatic instant credit via UPI app, QR</blockquote>\n"
        f"{DIV}\n"
        f"{pe('point')} <i>Choose an amount below to generate a QR.</i>"
    )

def wallet_kb():
    kb = types.InlineKeyboardMarkup(row_width=3)
    amts = [50, 100, 200, 300, 500, 1000, 2000, 5000]
    kb.add(*[IB(f"{CURRENCY}{a}", callback_data=f"dep_{a}", style=BTN_PRIMARY, icon=EID("money")) for a in amts])
    kb.add(IB("CUSTOM AMOUNT", callback_data="dep_custom", style=BTN_SUCCESS, icon=EID("pencil")))
    kb.add(IB("BACK TO MAIN MENU", callback_data="home", style=BTN_DANGER, icon=EID("home")))
    return kb

@bot.callback_query_handler(func=lambda c: c.data == "addbal")
def cb_addbal(c):
    bot.answer_callback_query(c.id)
    safe_edit(c.message.chat.id, c.message.message_id, wallet_text(c.from_user), wallet_kb())

@bot.callback_query_handler(func=lambda c: c.data.startswith("dep_"))
def cb_dep(c):
    data = c.data
    if data == "dep_custom":
        bot.answer_callback_query(c.id)
        msg = safe_send(c.message.chat.id,
                        f"{pe('money')} <b>Enter the amount</b> you want to deposit "
                        f"(min {CURRENCY}{get_setting('min_deposit','20')}):")
        bot.register_next_step_handler(msg, step_custom_amount)
        return
    amount = data.split("_")[1]
    bot.answer_callback_query(c.id)
    _show_deposit_qr(c.message.chat.id, c.from_user.id, amount, c.message.message_id)

def step_custom_amount(m):
    try:
        amount = float(re.sub(r'[^\d.]', '', m.text))
    except Exception:
        return safe_send(m.chat.id, f"{pe('cross')} Invalid amount. Try again.")
    min_dep = float(get_setting("min_deposit", "20"))
    if amount < min_dep:
        return safe_send(m.chat.id, f"{pe('cross')} Minimum deposit is {money(min_dep)}.")
    _show_deposit_qr(m.chat.id, m.from_user.id, amount)

def _upi_string(amount, note):
    upi = get_setting("upi_id", "qureshiotp@upi")
    name = get_setting("upi_name", STORE_NAME)
    from urllib.parse import quote
    return ("upi://pay?pa=%s&pn=%s&am=%.2f&cu=INR&tn=%s"
            % (quote(upi), quote(name), float(amount), quote(note)))

def _make_qr_png(amount, note):
    if not QR_OK:
        return None
    img = qrcode.make(_upi_string(amount, note))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

def _show_deposit_qr(chat_id, uid, amount, edit_message_id=None):
    amount = float(amount)
    _PENDING_DEPOSIT[uid] = amount
    upi = get_setting("upi_id", "qureshiotp@upi")
    qr_mode = get_setting("qr_mode", "static")
    qr_photo = get_setting("qr_photo", "")
    txt = (
        f"{pe('star')} <b>{esc(STORE_NAME)}</b> • {pe('card')} <b>Deposit Funds</b>\n"
        f"{DIV}\n"
        f"<blockquote>{pe('money')} <b>Amount:</b> <code>{money(amount)}</code>\n"
        f"{pe('bank')} <b>UPI ID:</b> <code>{esc(upi)}</code>\n"
        f"{pe('clock')} <b>Valid for:</b> 15 minutes</blockquote>\n"
        f"{DIV}\n"
        f"{pe('point')} Pay <b>{money(amount)}</b> to the UPI ID above.\n"
        f"{pe('bolt')} After paying, tap <b>I have paid</b> and send the 12-digit UTR / Ref No."
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(IB("✅ I have paid", callback_data=f"paid_{int(amount)}"),
           IB("⬅ Back", callback_data="addbal"))
    pay_link = _upi_string(amount, STORE_NAME)

    # QR behaviour is controlled from the admin panel (off / static / auto)
    if qr_mode == "off":
        safe_send(chat_id, txt + f"\n\n{pe('link')} Pay link:\n<code>{esc(pay_link)}</code>",
                  reply_markup=kb)
        if edit_message_id:
            try: bot.delete_message(chat_id, edit_message_id)
            except Exception: pass
        return
    if qr_mode == "static" and qr_photo:
        try:
            bot.send_photo(chat_id, qr_photo,
                           caption=txt + f"\n{pe('card')} <i>Scan the QR above to pay.</i>",
                           reply_markup=kb)
            if edit_message_id:
                try: bot.delete_message(chat_id, edit_message_id)
                except Exception: pass
            return
        except Exception as e:
            log.warning("send static qr failed: %s", e)
    png = _make_qr_png(amount, f"{STORE_NAME} deposit")
    if png:
        try:
            bot.send_photo(chat_id, png, caption=txt, reply_markup=kb)
            if edit_message_id:
                try: bot.delete_message(chat_id, edit_message_id)
                except Exception: pass
            return
        except Exception as e:
            log.warning("send qr failed: %s", e)
    safe_send(chat_id, txt + f"\n\n{pe('link')} Pay link:\n<code>{esc(pay_link)}</code>",
              reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("paid_"))
def cb_paid(c):
    amount = c.data.split("_")[1]
    bot.answer_callback_query(c.id)
    msg = safe_send(c.message.chat.id,
                    f"{pe('lock')} <b>Send your 12-digit UTR / Transaction ID</b>\n"
                    f"{DIV}\n"
                    f"{pe('money')} Amount: <code>{money(amount)}</code>\n"
                    f"{pe('point')} Paste the UTR/Ref number from your UPI app:")
    bot.register_next_step_handler(msg, step_utr, float(amount))

def step_utr(m, amount):
    utr = (m.text or "").strip()
    if len(re.sub(r'\D', '', utr)) < 6:
        return safe_send(m.chat.id, f"{pe('cross')} That doesn't look like a valid UTR. Try again.")
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("""INSERT INTO deposits(user_id,amount,utr,status,created_at)
                     VALUES(?,?,?,?,?)""",
                  (m.from_user.id, amount, utr, "pending", datetime.utcnow().isoformat()))
        dep_id = c.lastrowid
        con.commit(); con.close()
    safe_send(m.chat.id,
              f"{pe('check')} <b>Payment submitted!</b>\n"
              f"{DIV}\n"
              f"{pe('box')} Ref: <code>#{dep_id}</code>\n"
              f"{pe('money')} Amount: <code>{money(amount)}</code>\n"
              f"{pe('clock')} <i>Your deposit is being verified. You'll be credited shortly.</i>",
              reply_markup=types.InlineKeyboardMarkup().add(
                  IB("🏠 Main Menu", callback_data="home")))
    _notify_admins_deposit(dep_id, m.from_user, amount, utr)

def _notify_admins_deposit(dep_id, user, amount, utr):
    txt = (f"{pe('money')} <b>New Deposit Request</b>\n"
           f"{DIV}\n"
           f"{pe('user')} User: <a href='tg://user?id={user.id}'>{esc(user.first_name)}</a> "
           f"(<code>{user.id}</code>)\n"
           f"{pe('money')} Amount: <code>{money(amount)}</code>\n"
           f"{pe('link')} UTR: <code>{esc(utr)}</code>\n"
           f"{pe('box')} Ref: <code>#{dep_id}</code>")
    kb = types.InlineKeyboardMarkup()
    kb.add(IB("✅ Approve", callback_data=f"depok_{dep_id}"),
           IB("❌ Reject", callback_data=f"depno_{dep_id}"))
    for a in ADMIN_IDS:
        try:
            safe_send(a, txt, reply_markup=kb)
        except Exception:
            pass

@bot.callback_query_handler(func=lambda c: c.data.startswith("depok_") or c.data.startswith("depno_"))
def cb_dep_decision(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True)
        return
    approve = c.data.startswith("depok_")
    dep_id = int(c.data.split("_")[1])
    with _db_lock:
        con = db(); c2 = con.cursor()
        c2.execute("SELECT * FROM deposits WHERE id=?", (dep_id,))
        d = c2.fetchone()
        if not d:
            con.close(); bot.answer_callback_query(c.id, "Not found."); return
        d = dict(d)
        if d["status"] != "pending":
            con.close(); bot.answer_callback_query(c.id, "Already processed."); return
        c2.execute("UPDATE deposits SET status=?, credited_at=? WHERE id=?",
                   ("approved" if approve else "rejected",
                    datetime.utcnow().isoformat(), dep_id))
        if approve:
            c2.execute("UPDATE users SET balance = balance + ? WHERE user_id=?",
                       (d["amount"], d["user_id"]))
        con.commit(); con.close()
    bot.answer_callback_query(c.id, "Done.")
    if approve:
        safe_send(d["user_id"],
                  f"{pe('check')} <b>Deposit Approved!</b>\n"
                  f"{DIV}\n"
                  f"{pe('money')} <b>{money(d['amount'])}</b> credited to your wallet.\n"
                  f"{pe('gem')} Happy shopping!",
                  reply_markup=types.InlineKeyboardMarkup().add(
                      IB("🛒 Browse Accounts", callback_data="browse")))
    else:
        safe_send(d["user_id"], f"{pe('cross')} Your deposit was rejected. Contact support.")
    try:
        bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=None)
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════════════
#  ORDERS / PROFILE / TERMS
# ═══════════════════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data == "orders")
def cb_orders(c):
    bot.answer_callback_query(c.id)
    with _db_lock:
        con = db(); c2 = con.cursor()
        c2.execute("""SELECT o.*, a.country, a.cc, a.code, a.phone, a.label FROM orders o
                      LEFT JOIN accounts a ON a.id=o.account_id
                      WHERE o.user_id=? ORDER BY o.id DESC LIMIT 15""", (c.from_user.id,))
        rows = [dict(r) for r in c2.fetchall()]; con.close()
    if not rows:
        txt = (f"{pe('box')} <b>My Orders</b>\n{DIV}\n"
               f"{pe('cross')} You have no orders yet.")
    else:
        lines = [f"{pe('box')} <b>My Orders</b>", DIV]
        for o in rows:
            flag = pe_flag(o.get("cc"))
            st = {"awaiting_login": pe("clock") + " Awaiting login",
                  "completed": pe("check") + " Completed",
                  "cancelled": pe("cross") + " Cancelled"}.get(o["status"], o["status"])
            lines.append(f"{flag} <b>#{o['id']}</b> • {esc(o.get('country') or '-')} • "
                         f"{money(o['price'])} • {st}"
                         + (f" • {esc(o['label'])}" if o.get('label') else ""))
        txt = "\n".join(lines)
    safe_edit(c.message.chat.id, c.message.message_id, txt,
              types.InlineKeyboardMarkup().add(
                  IB("🏠 Main Menu", callback_data="home")))

@bot.callback_query_handler(func=lambda c: c.data == "profile")
def cb_profile(c):
    bot.answer_callback_query(c.id)
    u = c.from_user
    usr = get_user(u.id) or {}
    n_orders = _count("SELECT COUNT(*) n FROM orders WHERE user_id=?", (u.id,))
    n_done = _count("SELECT COUNT(*) n FROM orders WHERE user_id=? AND status='completed'", (u.id,))
    uname = u.username or "\u2014"
    txt = (f"{pe('crown')} <b>CLIENT PROFILE &amp; EXECUTIVE DASHBOARD</b>\n"
           f"{DIV}\n"
           f"<blockquote>{pe('id')} <b>Account ID:</b> <code>{u.id}</code>\n"
           f"{pe('user')} <b>Client Name:</b> {esc(u.first_name)}\n"
           f"{pe('link')} <b>Username:</b> @{esc(uname)}\n"
           f"{pe('medal')} <b>Membership:</b> Standard Member\n"
           f"{pe('check')} <b>Status:</b> Active</blockquote>\n"
           f"{DIV}\n"
           f"<blockquote>{pe('wallet')} <b>Available Balance:</b> <code>{money(usr.get('balance',0))}</code>\n"
           f"{pe('box')} <b>Accounts Delivered:</b> <b>{n_done}</b>\n"
           f"{pe('chart2')} <b>Total Orders:</b> <b>{n_orders}</b>\n"
           f"{pe('gear')} <b>API Gateway:</b> REST v1 Operational</blockquote>\n"
           f"{DIV}\n"
           f"{pe('bolt')} <i>Instant OTP delivery, automatic 2FA session management, and resettable passwords on the fly.</i>")
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(IB("DEVELOPER API", callback_data="devapi", style=BTN_PRIMARY, icon=EID("laptop")),
           IB("MY ORDERS", callback_data="orders", style=BTN_PRIMARY, icon=EID("box")))
    kb.add(IB("ADD BALANCE", callback_data="addbal", style=BTN_SUCCESS, icon=EID("card")),
           IB("SUPPORT", url=SUPPORT_URL, style=BTN_PRIMARY, icon=EID("shield")))
    kb.add(IB("BACK TO MAIN MENU", callback_data="home", style=BTN_DANGER, icon=EID("home")))
    safe_edit(c.message.chat.id, c.message.message_id, txt, kb)


@bot.callback_query_handler(func=lambda c: c.data == "proofs")
def cb_proofs(c):
    bot.answer_callback_query(c.id)
    txt = (f"{pe('doc')} <b>Payment Proofs</b>\n"
           f"{DIV}\n"
           f"<blockquote>{pe('check')} All successful deposits &amp; deliveries are posted here.\n"
           f"{pe('shield')} <i>100% transparent \u2014 verify before you buy.</i></blockquote>\n"
           f"{pe('point')} Open our channel to view the latest proofs.")
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(IB("VIEW PROOFS", url=CHANNEL_URL, style=BTN_SUCCESS, icon=EID("doc")))
    kb.add(IB("MAIN MENU", callback_data="home", style=BTN_DANGER, icon=EID("home")))
    safe_edit(c.message.chat.id, c.message.message_id, txt, kb)

def _api_link_valid(url):
    """True only for a real, openable http(s) URL (not the placeholder)."""
    if not url or not isinstance(url, str):
        return False
    u = url.strip().lower()
    if not (u.startswith("http://") or u.startswith("https://")):
        return False
    if "your-domain" in u or "example.com" in u or "localhost" in u:
        return False
    return True


@bot.callback_query_handler(func=lambda c: c.data in ("devapi", "devapi_regen"))
def cb_devapi(c):
  try:
    u = c.from_user
    if c.data == "devapi_regen":
        regenerate_api_key(u.id)
        bot.answer_callback_query(c.id, "New API key generated!")
    else:
        bot.answer_callback_query(c.id)
    usr = get_user(u.id) or {}
    key = get_or_create_api_key(u.id)
    base = get_api_base()
    sep = "&" if "?" in base else "?"
    open_url = f"{base}{sep}api_key={key}" if base else ""
    link_ok = _api_link_valid(open_url)
    _root = get_public_root()
    store_url = f"{_root}/store?api_key={key}" if _api_link_valid(_root) else ""
    if link_ok:
        link_block = (f"{pe('rocket')} <b>Your Personal API Link</b> (tap to open):\n"
                      f"{pe('link')} <a href=\"{esc(open_url)}\">{esc(open_url)}</a>\n")
    else:
        link_block = (f"{pe('gear')} <b>API Gateway:</b> <i>live endpoint is being provisioned.</i>\n"
                      f"{pe('key')} Use your key below as header <code>X-API-Key</code>.\n")
    txt = (f"{pe('laptop')} <b>{esc(STORE_NAME)} \u2022 REST API GATEWAY</b> {pe('lock')}\n"
           f"{DIV}\n"
           f"<blockquote>{pe('key')} <b>Secret API Key</b> (tap to copy)\n"
           f"<code>{esc(key)}</code>\n"
           f"{pe('money')} <b>Available Balance:</b> <code>{money(usr.get('balance',0))}</code>\n"
           f"{pe('globe')} <b>Base Endpoint:</b>\n<code>{esc(base or 'provisioning...')}</code>\n"
           f"{pe('gear')} <b>Rate Limit:</b> 120 req / min</blockquote>\n"
           f"{link_block}"
           f"{DIV}\n"
           f"{pe('rocket')} <b>Endpoints:</b>\n"
           f"{pe('point')} <code>GET  /balance</code> \u2014 your wallet balance\n"
           f"{pe('point')} <code>GET  /countries</code> \u2014 stock by country\n"
           f"{pe('point')} <code>GET  /accounts?country=India</code> \u2014 available accounts\n"
           f"{pe('point')} <code>POST /buy</code> \u2014 buy an account (auto-deducts balance)\n"
           f"{pe('point')} <code>GET  /orders</code> \u2014 your API orders\n"
           f"{DIV}\n"
           f"{pe('shield')} <i>Send your key as header <code>X-API-Key</code>. "
           f"Every purchase is deducted from YOUR balance \u2014 build your own store with it.</i>")
    kb = types.InlineKeyboardMarkup(row_width=1)
    if link_ok:
        kb.add(IB("🚀 OPEN MY API DASHBOARD", url=open_url, style=BTN_SUCCESS, icon=EID("rocket")))
    if store_url:
        kb.add(IB("🛒 OPEN MY WEB STORE", url=store_url, style=BTN_PRIMARY, icon=EID("cart")))
    kb.add(IB("READ API DOCUMENTATION", url=SUPPORT_URL, style=BTN_PRIMARY, icon=EID("book")))
    kb.add(IB("REGENERATE SECRET KEY", callback_data="devapi_regen", style=BTN_DANGER, icon=EID("refresh")))
    kb.add(IB("BACK TO PROFILE", callback_data="profile", style=BTN_PRIMARY, icon=EID("back")))
    safe_edit(c.message.chat.id, c.message.message_id, txt, kb)
  except Exception as e:
    log.error("cb_devapi error: %s", e)
    try:
        bot.answer_callback_query(c.id, "API panel error.", show_alert=False)
    except Exception:
        pass
    try:
        safe_send(c.message.chat.id,
                  f"{pe('cross')} <b>API panel error</b>\n{DIV}\n<code>{esc(e)}</code>")
    except Exception:
        pass

@bot.callback_query_handler(func=lambda c: c.data in ("terms", "terms_accept"))
def cb_terms(c):
    if c.data == "terms_accept":
        with _db_lock:
            con = db(); c2 = con.cursor()
            c2.execute("UPDATE users SET accepted_terms=1 WHERE user_id=?", (c.from_user.id,))
            con.commit(); con.close()
        bot.answer_callback_query(c.id, "Terms accepted — thank you!")
        return safe_edit(c.message.chat.id, c.message.message_id,
              f"{pe('check')} <b>Terms Accepted</b>\n{DIV}\n"
              f"{pe('shield')} <i>Thank you. You may now purchase with confidence.</i>",
              types.InlineKeyboardMarkup().add(
                  IB("BROWSE ACCOUNTS", callback_data="browse", style=BTN_SUCCESS, icon=EID("cart")),
                  IB("MAIN MENU", callback_data="home", style=BTN_DANGER, icon=EID("home"))))
    bot.answer_callback_query(c.id)
    safe_edit(c.message.chat.id, c.message.message_id,
              f"{terms_text()}",
              types.InlineKeyboardMarkup(row_width=1).add(
                  IB("✅ ACCEPT TERMS &amp; POLICIES", callback_data="terms_accept",
                     style=BTN_SUCCESS, icon=EID("check")),
                  IB("MAIN MENU", callback_data="home", style=BTN_DANGER, icon=EID("home"))))

@bot.callback_query_handler(func=lambda c: c.data == "referral")
def cb_referral(c):
    bot.answer_callback_query(c.id)
    usr = get_user(c.from_user.id) or {}
    link = referral_link(c.from_user.id)
    reward = money(get_setting("referral_reward", "1"))
    txt = (f"{pe('gift')} <b>Refer &amp; Earn</b> {pe('party')}\n"
           f"{DIV}\n"
           f"<blockquote>{pe('money')} <b>Reward per referral:</b> <code>{reward}</code>\n"
           f"{pe('user')} <b>Total Referrals:</b> <b>{usr.get('ref_count', 0)}</b>\n"
           f"{pe('wallet')} <b>Total Earned:</b> <code>{money(usr.get('ref_earned', 0))}</code></blockquote>\n"
           f"{pe('link')} <b>Your Referral Link:</b>\n<code>{esc(link)}</code>\n"
           f"{DIV}\n"
           f"{pe('point')} <b>How it works:</b>\n"
           f"{pe('point')} <b>1.</b> Share your link with friends.\n"
           f"{pe('point')} <b>2.</b> They start the bot with your link.\n"
           f"{pe('point')} <b>3.</b> Once they <b>join our channel</b>, you get {reward} instantly.\n"
           f"{DIV}\n"
           f"{pe('warn')} <i>Fake / self referrals are not counted and may lead to a ban.</i>")
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(IB("SHARE LINK", url="https://t.me/share/url?url=" + link +
              "&text=Join%20the%20best%20Telegram%20account%20store!",
              style=BTN_SUCCESS, icon=EID("send")))
    kb.add(IB("MAIN MENU", callback_data="home", style=BTN_DANGER, icon=EID("home")))
    safe_edit(c.message.chat.id, c.message.message_id, txt, kb)

# ═══════════════════════════════════════════════════════════════════════════
#  ADMIN PANEL
# ═══════════════════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data == "redeem")
def cb_redeem(c):
    bot.answer_callback_query(c.id)
    msg = safe_send(c.message.chat.id,
                    f"{pe('gem')} <b>Redeem Code</b>\n{DIV}\n"
                    f"{pe('point')} Send your <b>redeem code</b> to add balance:")
    bot.register_next_step_handler(msg, step_redeem_user)

def step_redeem_user(m):
    code = (m.text or "").strip().upper()
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("SELECT * FROM redeem_codes WHERE code=?", (code,))
        r = c.fetchone()
        if not r:
            con.close()
            return safe_send(m.chat.id, f"{pe('cross')} Invalid code.",
                             reply_markup=types.InlineKeyboardMarkup().add(
                                 IB("⬅ Main Menu", callback_data="home")))
        r = dict(r)
        if not r["active"] or r["uses"] >= r["max_uses"]:
            con.close()
            return safe_send(m.chat.id, f"{pe('cross')} This code is expired or fully used.",
                             reply_markup=types.InlineKeyboardMarkup().add(
                                 IB("⬅ Main Menu", callback_data="home")))
        used = (r["used_by"] or "").split(",")
        if str(m.from_user.id) in used:
            con.close()
            return safe_send(m.chat.id, f"{pe('cross')} You already used this code.",
                             reply_markup=types.InlineKeyboardMarkup().add(
                                 IB("⬅ Main Menu", callback_data="home")))
        used.append(str(m.from_user.id))
        c.execute("UPDATE redeem_codes SET uses=uses+1, used_by=? WHERE code=?",
                  (",".join(used), code))
        c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (r["amount"], m.from_user.id))
        con.commit(); con.close()
    bal = (get_user(m.from_user.id) or {}).get("balance", 0)
    safe_send(m.chat.id,
              f"{pe('check')} <b>Code redeemed!</b>\n{DIV}\n"
              f"{pe('money')} Added: <code>{money(r['amount'])}</code>\n"
              f"{pe('wallet')} New Balance: <code>{money(bal)}</code>",
              reply_markup=types.InlineKeyboardMarkup().add(
                  IB("🛒 Browse Accounts", callback_data="browse"),
                  IB("⬅ Main Menu", callback_data="home")))

ADMIN_STATE = {}   # uid -> dict for multi-step flows

def admin_menu_text():
    n_avail = _count("SELECT COUNT(*) n FROM accounts WHERE status='available'")
    n_sold = _count("SELECT COUNT(*) n FROM accounts WHERE status='sold'")
    n_users = _count("SELECT COUNT(*) n FROM users")
    n_orders = _count("SELECT COUNT(*) n FROM orders")
    ref_on = str(get_setting("referral_enabled", "1")) != "0"
    qr_mode = get_setting("qr_mode", "static")
    maint = "ON" if maintenance_on() else "OFF"
    return (
        f"{pe('crown')} <b>Admin Control Panel</b> {pe('gear')}\n"
        f"{DIV}\n"
        f"{pe('chart')} Accounts: <b>{n_avail}</b> available • <b>{n_sold}</b> sold\n"
        f"{pe('user')} Users: <b>{n_users}</b> • {pe('cart')} Orders: <b>{n_orders}</b>\n"
        f"{pe('bank')} UPI: <code>{esc(get_setting('upi_id'))}</code>\n"
        f"{pe('money')} Min Deposit: <code>{CURRENCY}{esc(get_setting('min_deposit'))}</code>\n"
        f"{pe('gift')} Referral: <b>{'ON' if ref_on else 'OFF'}</b> • "
        f"{pe('card')} QR: <b>{esc(qr_mode)}</b> • {pe('tool')} Maint: <b>{maint}</b>\n"
        f"{DIV}\n"
        f"{pe('point')} Choose an action below.")

def admin_menu_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(IB("➕ Add Account", callback_data="adm_addacc", style=BTN_SUCCESS),
           IB("🗂 Manage Accounts", callback_data="adm_manage", style=BTN_PRIMARY))
    kb.add(IB("👥 Users", callback_data="adm_users", style=BTN_SUCCESS),
           IB("🔍 Find User", callback_data="adm_finduser", style=BTN_PRIMARY))
    kb.add(IB("💰 Balance Manager", callback_data="adm_balmgr", style=BTN_SUCCESS),
           IB("🎟 Redeem Codes", callback_data="adm_redeem", style=BTN_SUCCESS))
    kb.add(IB("🔗 Force Join", callback_data="adm_fj", style=BTN_PRIMARY),
           IB("💳 Deposits", callback_data="adm_deps", style=BTN_SUCCESS))
    kb.add(IB("📊 Statistics", callback_data="adm_stats", style=BTN_PRIMARY),
           IB("🧾 Sales Report", callback_data="adm_sales", style=BTN_SUCCESS))
    kb.add(IB("🎁 Referral", callback_data="adm_referral", style=BTN_SUCCESS),
           IB("⚙️ Settings", callback_data="adm_settings", style=BTN_PRIMARY))
    kb.add(IB("📣 Broadcast", callback_data="adm_bc", style=BTN_PRIMARY),
           IB("🎨 Emoji IDs", callback_data="adm_emoji", style=BTN_PRIMARY))
    kb.add(IB("🖼 Menu Photo", callback_data="adm_photo", style=BTN_PRIMARY),
           IB("🛠 Maintenance", callback_data="adm_maint", style=BTN_DANGER))
    kb.add(IB("📣 Proof Channel", callback_data="adm_proof", style=BTN_SUCCESS),
           IB("🔌 API Accounts", callback_data="adm_api", style=BTN_SUCCESS))
    kb.add(IB("🏠 Main Menu", callback_data="home", style=BTN_PRIMARY))
    return kb

@bot.message_handler(commands=["admin", "panel"])
def cmd_admin(m):
    if not is_admin(m.from_user.id):
        safe_send(m.chat.id,
                  f"{pe('lock')} <b>Admins only.</b>\n"
                  f"{DIV}\n{pe('id')} Your ID: <code>{m.from_user.id}</code>\n"
                  f"{pe('info')} Ask the owner to add you as admin.")
        return
    safe_send(m.chat.id, admin_menu_text(), reply_markup=admin_menu_kb())

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_"))
def cb_admin(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    data = c.data
    if data == "adm_menu":
        bot.answer_callback_query(c.id)
        safe_edit(c.message.chat.id, c.message.message_id, admin_menu_text(), admin_menu_kb())
        return
    if data == "adm_addacc":
        bot.answer_callback_query(c.id)
        _show_addacc_platform(c.message.chat.id, c.message.message_id)
        return
    if data == "adm_proof":
        bot.answer_callback_query(c.id)
        _show_proof_menu(c.message.chat.id, c.message.message_id)
        return
    if data == "adm_list":
        bot.answer_callback_query(c.id)
        with _db_lock:
            con = db(); c2 = con.cursor()
            c2.execute("SELECT * FROM accounts ORDER BY id DESC LIMIT 25")
            rows = [dict(r) for r in c2.fetchall()]; con.close()
        if not rows:
            txt = f"{pe('cross')} No accounts yet."
        else:
            lines = [f"{pe('box')} <b>Accounts</b>", DIV]
            for r in rows:
                lines.append(f"#{r['id']} {pe_flag(r['cc'])} {esc(r['country'] or '-')} "
                             f"<code>{esc(r['phone'])}</code> • {money(r['price'])} • {r['status']}"
                             + (f" • {esc(r['label'])}" if r.get('label') else ""))
            txt = "\n".join(lines)
        safe_edit(c.message.chat.id, c.message.message_id, txt,
                  types.InlineKeyboardMarkup().add(
                      IB("⬅ Back", callback_data="adm_menu")))
        return
    if data == "adm_users":
        bot.answer_callback_query(c.id)
        _show_users(c.message.chat.id, c.message.message_id, 0)
        return
    if data == "adm_fj":
        bot.answer_callback_query(c.id)
        _show_fj_menu(c.message.chat.id, c.message.message_id)
        return
    if data == "adm_settings":
        bot.answer_callback_query(c.id)
        _show_settings(c.message.chat.id, c.message.message_id)
        return
    if data == "adm_deps":
        bot.answer_callback_query(c.id)
        _show_deposits(c.message.chat.id, c.message.message_id)
        return
    if data == "adm_bc":
        bot.answer_callback_query(c.id)
        msg = safe_send(c.message.chat.id, f"{pe('chart')} <b>Broadcast</b>\n{DIV}\n"
                                           f"{pe('point')} Send the message to broadcast to all users:")
        bot.register_next_step_handler(msg, step_broadcast)
        return
    if data == "adm_emoji":
        bot.answer_callback_query(c.id)
        msg = safe_send(c.message.chat.id,
                        f"{pe('gem')} <b>Emoji IDs</b>\n{DIV}\n"
                        f"{pe('point')} Send a <b>premium emoji</b> (from a Premium account) and "
                        f"I'll capture its ID. Send it as <code>key=</code> then the emoji, e.g. {pe('star')}\n"
                        f"For a <b>country flag</b> send <code>flag IN=</code> then the flag {pe_flag('IN')}")
        bot.register_next_step_handler(msg, step_capture_emoji)
        return
    if data == "adm_manage":
        bot.answer_callback_query(c.id)
        _show_manage(c.message.chat.id, c.message.message_id, 0)
        return
    if data == "adm_finduser":
        bot.answer_callback_query(c.id)
        msg = safe_send(c.message.chat.id,
                        f"{pe('search')} <b>Find User</b>\n{DIV}\n"
                        f"{pe('point')} Send the <b>user ID</b> to look up:")
        bot.register_next_step_handler(msg, step_finduser)
        return
    if data == "adm_balmgr":
        bot.answer_callback_query(c.id)
        msg = safe_send(c.message.chat.id,
                        f"{pe('money')} <b>Balance Manager</b>\n{DIV}\n"
                        f"{pe('point')} Send the <b>user ID</b> whose balance you want to change:")
        bot.register_next_step_handler(msg, step_balmgr_uid)
        return
    if data == "adm_redeem":
        bot.answer_callback_query(c.id)
        _show_redeem(c.message.chat.id, c.message.message_id)
        return
    if data == "adm_stats":
        bot.answer_callback_query(c.id)
        _show_stats(c.message.chat.id, c.message.message_id)
        return
    if data == "adm_sales":
        bot.answer_callback_query(c.id)
        _show_sales(c.message.chat.id, c.message.message_id)
        return
    if data == "adm_referral":
        bot.answer_callback_query(c.id)
        _show_referral_admin(c.message.chat.id, c.message.message_id)
        return
    if data == "adm_maint":
        bot.answer_callback_query(c.id)
        _show_maint(c.message.chat.id, c.message.message_id)
        return
    if data == "adm_photo":
        bot.answer_callback_query(c.id)
        msg = safe_send(c.message.chat.id, f"{pe('gem')} <b>Menu Photo</b>\n{DIV}\n"
                                           f"{pe('point')} Send a photo to use as the main-menu banner:")
        bot.register_next_step_handler(msg, step_menu_photo)
        return
    if data == "adm_api":
        bot.answer_callback_query(c.id)
        _show_api_admin(c.message.chat.id, c.message.message_id)
        return

# ── User management ──────────────────────────────────────────────────────
# ── Manage Accounts ───────────────────────────────────────────────────────────
MANAGE_PER_PAGE = 12

def _show_manage(chat_id, mid, page=0):
    with _db_lock:
        con = db(); c = con.cursor()
        total = c.execute("SELECT COUNT(*) n FROM accounts").fetchone()["n"]
        c.execute("SELECT * FROM accounts ORDER BY id DESC LIMIT ? OFFSET ?",
                  (MANAGE_PER_PAGE, page * MANAGE_PER_PAGE))
        rows = [dict(r) for r in c.fetchall()]; con.close()
    total_pages = max(1, (total + MANAGE_PER_PAGE - 1) // MANAGE_PER_PAGE)
    lines = [f"{pe('folder')} <b>Manage Accounts</b>", DIV,
             f"{pe('chart')} Total: <b>{total}</b> \u2022 Page <b>{page+1}/{total_pages}</b>", ""]
    kb = types.InlineKeyboardMarkup(row_width=1)
    if not rows:
        lines.append(f"{pe('cross')} No accounts.")
    for r in rows:
        st = r["status"]
        emo = {"available": "\u2705", "sold": "\U0001F534", "disabled": "\u26d4",
               "pending": "\u23f3"}.get(st, "\u2022")
        lbl = (r.get("label") or "").strip()
        t = f"{emo} #{r['id']} {r['cc'] or ''} {r['code'] or ''} \u2022 {money(r['price'])} \u2022 {st}"
        if lbl:
            t += f" \u2022 {lbl}"
        kb.add(IB(t, callback_data=f"mng_{r['id']}"))
    nav = []
    if page > 0:
        nav.append(IB("\u2b05 Prev", callback_data=f"mngpg_{page-1}"))
    if page + 1 < total_pages:
        nav.append(IB("Next \u27a1", callback_data=f"mngpg_{page+1}"))
    if nav:
        kb.row(*nav)
    kb.add(IB("\u2b05 Back", callback_data="adm_menu"))
    safe_edit(chat_id, mid, "\n".join(lines), kb)

def _render_manage_item(chat_id, mid, aid):
    acc = get_account(aid)
    if not acc:
        safe_edit(chat_id, mid, f"{pe('cross')} Account not found.",
                  types.InlineKeyboardMarkup().add(IB("\u2b05 Back", callback_data="adm_manage")))
        return
    txt = (f"{pe('box')} <b>Account #{acc['id']}</b>\n{DIV}\n"
           f"{pe_flag(acc.get('cc'))} {esc(acc.get('country') or '-')} \u2022 "
           f"<code>{esc(acc.get('code') or '')}</code>\n"
           f"{pe('phone')} <code>{esc(acc.get('phone') or '')}</code>\n"
           f"{pe('tag')} Label: {esc(acc.get('label') or 'Standard')}\n"
           f"{pe('money')} Price: <code>{money(acc.get('price', 0))}</code>\n"
           f"{pe('chart')} Status: <b>{acc.get('status')}</b>\n"
           f"{pe('user')} Sold to: <code>{acc.get('sold_to') or '-'}</code>")
    kb = types.InlineKeyboardMarkup(row_width=2)
    if acc.get("status") == "available":
        kb.add(IB("\u26d4 Disable", callback_data=f"mngact_disable_{aid}", style=BTN_DANGER),
               IB("\u270f\ufe0f Price", callback_data=f"mngact_price_{aid}", style=BTN_PRIMARY))
    elif acc.get("status") == "disabled":
        kb.add(IB("\u2705 Enable", callback_data=f"mngact_enable_{aid}", style=BTN_SUCCESS),
               IB("\u270f\ufe0f Price", callback_data=f"mngact_price_{aid}", style=BTN_PRIMARY))
    else:
        kb.add(IB("\u270f\ufe0f Price", callback_data=f"mngact_price_{aid}", style=BTN_PRIMARY))
    kb.add(IB("\U0001F5D1 Delete", callback_data=f"mngact_del_{aid}", style=BTN_DANGER))
    kb.add(IB("\u2b05 Back", callback_data="adm_manage"))
    safe_edit(chat_id, mid, txt, kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("mngpg_"))
def cb_manage_page(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    bot.answer_callback_query(c.id)
    try:
        page = int(c.data.split("_")[1])
    except Exception:
        page = 0
    _show_manage(c.message.chat.id, c.message.message_id, page)

@bot.callback_query_handler(func=lambda c: c.data.startswith("mng_"))
def cb_manage_item(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    bot.answer_callback_query(c.id)
    try:
        aid = int(c.data.split("_")[1])
    except Exception:
        return
    _render_manage_item(c.message.chat.id, c.message.message_id, aid)

@bot.callback_query_handler(func=lambda c: c.data.startswith("mngact_"))
def cb_manage_action(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    parts = c.data.split("_")
    action = parts[1]; aid = int(parts[2])
    if action == "price":
        bot.answer_callback_query(c.id)
        msg = safe_send(c.message.chat.id,
                        f"{pe('money')} Send the <b>new price</b> for account #{aid}:")
        bot.register_next_step_handler(msg, step_manage_price, aid)
        return
    with _db_lock:
        con = db(); c2 = con.cursor()
        if action == "disable":
            c2.execute("UPDATE accounts SET status='disabled' WHERE id=?", (aid,))
        elif action == "enable":
            c2.execute("UPDATE accounts SET status='available' WHERE id=?", (aid,))
        elif action == "del":
            c2.execute("DELETE FROM accounts WHERE id=?", (aid,))
        con.commit(); con.close()
    bot.answer_callback_query(c.id, "Done.")
    if action == "del":
        _show_manage(c.message.chat.id, c.message.message_id, 0)
    else:
        _render_manage_item(c.message.chat.id, c.message.message_id, aid)

def step_manage_price(m, aid):
    if not is_admin(m.from_user.id):
        return
    try:
        price = float(re.sub(r'[^\d.]', '', m.text or ""))
    except Exception:
        return safe_send(m.chat.id, f"{pe('cross')} Invalid price.")
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("UPDATE accounts SET price=? WHERE id=?", (price, aid))
        con.commit(); con.close()
    safe_send(m.chat.id, f"{pe('check')} Price updated to <code>{money(price)}</code> for #{aid}.",
              reply_markup=types.InlineKeyboardMarkup().add(
                  IB("\U0001F5C2 Manage", callback_data="adm_manage")))

# ── Find User ─────────────────────────────────────────────────────────────────
def step_finduser(m):
    if not is_admin(m.from_user.id):
        return
    uid = re.sub(r'\D', '', m.text or "")
    if not uid:
        return safe_send(m.chat.id, f"{pe('cross')} Invalid ID.")
    uid = int(uid)
    r = get_user(uid)
    if not r:
        return safe_send(m.chat.id, f"{pe('cross')} No user with ID <code>{uid}</code>.",
                         reply_markup=types.InlineKeyboardMarkup().add(
                             IB("\u2b05 Admin", callback_data="adm_menu")))
    safe_send(m.chat.id, _user_detail_text(r), reply_markup=_user_detail_kb(uid, r.get("banned")))

# ── Balance Manager (by user ID) ─────────────────────────────────────────────
def step_balmgr_uid(m):
    if not is_admin(m.from_user.id):
        return
    uid = re.sub(r'\D', '', m.text or "")
    if not uid:
        return safe_send(m.chat.id, f"{pe('cross')} Invalid user ID.")
    uid = int(uid)
    r = get_user(uid)
    if not r:
        return safe_send(m.chat.id, f"{pe('cross')} No user with ID <code>{uid}</code>.",
                         reply_markup=types.InlineKeyboardMarkup().add(
                             IB("⬅ Admin", callback_data="adm_menu")))
    safe_send(m.chat.id,
              f"{pe('money')} <b>Balance Manager</b>\n{DIV}\n"
              f"{pe('user')} User: <code>{uid}</code>\n"
              f"{pe('wallet')} Current Balance: <code>{money(r.get('balance', 0))}</code>\n"
              f"{DIV}\n{pe('point')} Choose an action:",
              reply_markup=_user_detail_kb(uid, r.get("banned")))

# ── Redeem Codes ─────────────────────────────────────────────────────────────
def _redeem_list():
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("SELECT * FROM redeem_codes ORDER BY created_at DESC LIMIT 30")
        rows = [dict(r) for r in c.fetchall()]; con.close()
    return rows

def _show_redeem(chat_id, mid):
    rows = _redeem_list()
    lines = [f"{pe('gift')} <b>Redeem Codes</b>", DIV]
    if not rows:
        lines.append(f"{pe('cross')} No codes yet. Tap <b>Generate</b> below.")
    for r in rows:
        st = "✅" if r["active"] and r["uses"] < r["max_uses"] else "⛔"
        lines.append(f"{st} <code>{esc(r['code'])}</code> • {money(r['amount'])} • "
                     f"{r['uses']}/{r['max_uses']} used")
    lines.append(DIV)
    lines.append(f"{pe('point')} Generate a code, then share it. Users redeem in the bot.")
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(IB("➕ Generate New Code", callback_data="rdm_new", style=BTN_SUCCESS, icon=EID("gift")))
    kb.add(IB("🗑 Delete All Codes", callback_data="rdm_clear", style=BTN_DANGER, icon=EID("cross")))
    kb.add(IB("⬅ Admin", callback_data="adm_menu", style=BTN_PRIMARY))
    safe_edit(chat_id, mid, "\n".join(lines), kb)

@bot.callback_query_handler(func=lambda c: c.data in ("rdm_new", "rdm_clear"))
def cb_redeem_admin(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    if c.data == "rdm_clear":
        with _db_lock:
            con = db(); c2 = con.cursor()
            c2.execute("DELETE FROM redeem_codes")
            con.commit(); con.close()
        bot.answer_callback_query(c.id, "All codes deleted.")
        _show_redeem(c.message.chat.id, c.message.message_id)
        return
    bot.answer_callback_query(c.id)
    msg = safe_send(c.message.chat.id,
                    f"{pe('gift')} <b>Generate Redeem Code</b>\n{DIV}\n"
                    f"{pe('point')} Send the <b>amount</b> (e.g. <code>20</code>)\n"
                    f"Optionally add uses: <code>20 5</code> = ₹20 code usable 5 times.")
    bot.register_next_step_handler(msg, step_redeem_amount)

def step_redeem_amount(m):
    if not is_admin(m.from_user.id):
        return
    parts = (m.text or "").replace(CURRENCY, "").split()
    try:
        amt = float(parts[0])
    except Exception:
        return safe_send(m.chat.id, f"{pe('cross')} Invalid amount.")
    uses = 1
    if len(parts) > 1:
        try:
            uses = max(1, int(parts[1]))
        except Exception:
            uses = 1
    code = "QRS-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("""INSERT INTO redeem_codes(code,amount,max_uses,uses,created_at,active)
                     VALUES(?,?,?,0,?,1)""",
                  (code, amt, uses, datetime.utcnow().isoformat()))
        con.commit(); con.close()
    safe_send(m.chat.id,
              f"{pe('check')} <b>Redeem code created!</b>\n{DIV}\n"
              f"{pe('gift')} Code: <code>{code}</code>\n"
              f"{pe('money')} Amount: <code>{money(amt)}</code>\n"
              f"{pe('chart2')} Max uses: <b>{uses}</b>\n{DIV}\n"
              f"{pe('point')} Share this code with users.",
              reply_markup=types.InlineKeyboardMarkup().add(
                  IB("🎟 Redeem Codes", callback_data="adm_redeem"),
                  IB("⬅ Admin", callback_data="adm_menu")))

# ── Statistics ────────────────────────────────────────────────────────────────
def _show_stats(chat_id, mid):
    n_users = _count("SELECT COUNT(*) n FROM users")
    n_banned = _count("SELECT COUNT(*) n FROM users WHERE banned=1")
    n_avail = _count("SELECT COUNT(*) n FROM accounts WHERE status='available'")
    n_sold = _count("SELECT COUNT(*) n FROM accounts WHERE status='sold'")
    n_disp = _count("SELECT COUNT(*) n FROM accounts WHERE status='disabled'")
    n_orders = _count("SELECT COUNT(*) n FROM orders")
    n_done = _count("SELECT COUNT(*) n FROM orders WHERE status='completed'")
    n_dep = _count("SELECT COUNT(*) n FROM deposits")
    n_depok = _count("SELECT COUNT(*) n FROM deposits WHERE status='approved'")
    with _db_lock:
        con = db(); c = con.cursor()
        rev = c.execute("SELECT COALESCE(SUM(price),0) s FROM orders WHERE status='completed'").fetchone()["s"]
        dep = c.execute("SELECT COALESCE(SUM(amount),0) s FROM deposits WHERE status='approved'").fetchone()["s"]
        bal = c.execute("SELECT COALESCE(SUM(balance),0) s FROM users").fetchone()["s"]
        refs = c.execute("SELECT COUNT(*) n FROM referrals").fetchone()["n"]
        con.close()
    txt = (f"{pe('chart')} <b>Store Statistics</b>\n{DIV}\n"
           f"{pe('user')} Users: <b>{n_users}</b> (banned {n_banned})\n"
           f"{pe('box')} Accounts: <b>{n_avail}</b> available \u2022 <b>{n_sold}</b> sold \u2022 "
           f"<b>{n_disp}</b> disabled\n"
           f"{pe('cart')} Orders: <b>{n_orders}</b> \u2022 completed <b>{n_done}</b>\n"
           f"{pe('money')} Sales revenue: <code>{money(rev)}</code>\n"
           f"{pe('card')} Deposits: <b>{n_dep}</b> \u2022 approved <b>{n_depok}</b> \u2022 "
           f"<code>{money(dep)}</code>\n"
           f"{pe('wallet')} Total user balance: <code>{money(bal)}</code>\n"
           f"{pe('gift')} Referrals: <b>{refs}</b>")
    safe_edit(chat_id, mid, txt, types.InlineKeyboardMarkup().add(
        IB("\u2b05 Back", callback_data="adm_menu")))

# ── Sales Report ──────────────────────────────────────────────────────────────
def _show_sales(chat_id, mid):
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("""SELECT o.id,o.user_id,o.price,o.status,o.created_at,a.country,a.cc,a.label
                     FROM orders o LEFT JOIN accounts a ON a.id=o.account_id
                     ORDER BY o.id DESC LIMIT 20""")
        rows = [dict(r) for r in c.fetchall()]; con.close()
    lines = [f"{pe('doc')} <b>Recent Sales</b>", DIV]
    if not rows:
        lines.append(f"{pe('cross')} No sales yet.")
    for r in rows:
        lines.append(f"#{r['id']} {pe_flag(r.get('cc'))} {esc(r.get('country') or '-')} \u2022 "
                     f"{money(r['price'])} \u2022 <code>{r['user_id']}</code> \u2022 {r['status']}"
                     + (f" \u2022 {esc(r['label'])}" if r.get('label') else ""))
    safe_edit(chat_id, mid, "\n".join(lines), types.InlineKeyboardMarkup().add(
        IB("\u2b05 Back", callback_data="adm_menu")))

# ── Referral (admin) ──────────────────────────────────────────────────────────
def _show_referral_admin(chat_id, mid):
    ref_on = str(get_setting("referral_enabled", "1")) != "0"
    reward = money(get_setting("referral_reward", "1"))
    n_ref = _count("SELECT COUNT(*) n FROM referrals")
    txt = (f"{pe('gift')} <b>Referral Program</b>\n{DIV}\n"
           f"{pe('check')} Status: <b>{'ON' if ref_on else 'OFF'}</b>\n"
           f"{pe('money')} Reward per refer: <code>{reward}</code>\n"
           f"{pe('users')} Total referrals: <b>{n_ref}</b>\n"
           f"{DIV}\n{pe('info')} <i>Rewards are credited only after the invited user joins "
           f"the force-join channel \u2014 fake referrals never count.</i>")
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(IB("\U0001F381 Set Reward", callback_data="set_refreward"),
           IB("\U0001F501 Toggle On/Off", callback_data="set_reftoggle"),
           IB("\u2b05 Back", callback_data="adm_menu"))
    safe_edit(chat_id, mid, txt, kb)

# ── Maintenance ───────────────────────────────────────────────────────────────
def maintenance_on():
    return str(get_setting("maintenance", "0")) == "1"

def _show_maint(chat_id, mid):
    on = maintenance_on()
    txt = (f"{pe('tool')} <b>Maintenance Mode</b>\n{DIV}\n"
           f"Status: <b>{'ON' if on else 'OFF'}</b>\n"
           f"{pe('info')} <i>When ON, only admins can use the bot; users see a maintenance notice.</i>")
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(IB(("\u2705 Turn OFF" if on else "\U0001F6E0 Turn ON"), callback_data="maint_toggle",
              style=(BTN_SUCCESS if on else BTN_DANGER)))
    kb.add(IB("\u2b05 Back", callback_data="adm_menu"))
    safe_edit(chat_id, mid, txt, kb)

@bot.callback_query_handler(func=lambda c: c.data == "maint_toggle")
def cb_maint_toggle(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    cur = str(get_setting("maintenance", "0"))
    set_setting("maintenance", "0" if cur == "1" else "1")
    bot.answer_callback_query(c.id, "Toggled.")
    _show_maint(c.message.chat.id, c.message.message_id)

USERS_PER_PAGE = 10

def set_balance(uid, amount):
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("UPDATE users SET balance=? WHERE user_id=?", (amount, uid))
        con.commit(); con.close()

def set_banned(uid, flag):
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("UPDATE users SET banned=? WHERE user_id=?", (1 if flag else 0, uid))
        con.commit(); con.close()

def _user_rows(page=0):
    with _db_lock:
        con = db(); c = con.cursor()
        total = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        c.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
                  (USERS_PER_PAGE, page * USERS_PER_PAGE))
        rows = [dict(r) for r in c.fetchall()]; con.close()
        return rows, total

def _show_users(chat_id, mid, page=0):
    rows, total = _user_rows(page)
    total_pages = max(1, (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
    lines = [f"{pe('user')} <b>User Manager</b>", DIV,
             f"{pe('chart')} Total users: <b>{total}</b> \u2022 Page <b>{page+1}/{total_pages}</b>", ""]
    kb = types.InlineKeyboardMarkup(row_width=1)
    if not rows:
        lines.append(f"{pe('cross')} No users yet.")
    for r in rows:
        tag = " \U0001f6ab" if r.get("banned") else ""
        name = esc(r.get("first_name") or "User")
        kb.add(IB(f"\U0001f464 {name} \u2022 {money(r.get('balance', 0))}{tag}",
                  callback_data=f"user_{r['user_id']}"))
    nav = []
    if page > 0:
        nav.append(IB("\u2b05 Prev", callback_data=f"users_{page-1}"))
    if page + 1 < total_pages:
        nav.append(IB("Next \u27a1", callback_data=f"users_{page+1}"))
    if nav:
        kb.row(*nav)
    kb.add(IB("\u2b05 Back", callback_data="adm_menu"))
    safe_edit(chat_id, mid, "\n".join(lines), kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("users_"))
def cb_users_page(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    bot.answer_callback_query(c.id)
    try:
        page = int(c.data.split("_")[1])
    except Exception:
        page = 0
    _show_users(c.message.chat.id, c.message.message_id, page)

def _user_detail_text(r):
    uname = ("@" + r["username"]) if r.get("username") else "\u2014"
    return (
        f"{pe('user')} <b>User Details</b>\n"
        f"{DIV}\n"
        f"{pe('id')} ID: <code>{r['user_id']}</code>\n"
        f"{pe('point')} Name: <b>{esc(r.get('first_name') or '-')}</b>\n"
        f"{pe('link')} Username: {esc(uname)}\n"
        f"{pe('wallet')} Balance: <code>{money(r.get('balance', 0))}</code>\n"
        f"{pe('gem')} Points: <b>{r.get('points', 0)}</b>\n"
        f"{pe('warn') if r.get('banned') else pe('check')} Status: "
        f"<b>{'BANNED' if r.get('banned') else 'Active'}</b>"
    )

def _user_detail_kb(uid, banned):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(IB("\u270f\ufe0f Set Balance", callback_data=f"userbal_{uid}_set", style=BTN_PRIMARY),
           IB("\u2795 Add Funds", callback_data=f"userbal_{uid}_add", style=BTN_SUCCESS))
    kb.add(IB("\u2796 Subtract", callback_data=f"userbal_{uid}_sub", style=BTN_DANGER),
           IB(("\u2705 Unban" if banned else "\U0001f6ab Ban"), callback_data=f"userban_{uid}",
              style=(BTN_SUCCESS if banned else BTN_DANGER)))
    kb.add(IB("\u2b05 Back", callback_data="adm_users"))
    return kb

@bot.callback_query_handler(func=lambda c: c.data.startswith("user_") and not c.data.startswith("userbal_") and not c.data.startswith("userban_"))
def cb_user_detail(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    bot.answer_callback_query(c.id)
    uid = int(c.data.split("_")[1])
    r = get_user(uid)
    if not r:
        safe_edit(c.message.chat.id, c.message.message_id, f"{pe('cross')} User not found.",
                  types.InlineKeyboardMarkup().add(IB("\u2b05 Back", callback_data="adm_users")))
        return
    safe_edit(c.message.chat.id, c.message.message_id, _user_detail_text(r),
              _user_detail_kb(uid, r.get("banned")))

@bot.callback_query_handler(func=lambda c: c.data.startswith("userbal_"))
def cb_userbal(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    bot.answer_callback_query(c.id)
    parts = c.data.split("_")
    uid = int(parts[1]); mode = parts[2]
    prompt = {"set": "Send the <b>new balance</b> (absolute amount, e.g. <code>500</code>)",
              "add": "Send the amount to <b>add</b>",
              "sub": "Send the amount to <b>subtract</b>"}[mode]
    msg = safe_send(c.message.chat.id, f"{pe('money')} {prompt}:")
    bot.register_next_step_handler(msg, step_user_balance, uid, mode)

def step_user_balance(m, uid, mode):
    if not is_admin(m.from_user.id):
        return
    raw = (m.text or "").strip().replace(CURRENCY, "").replace(",", "")
    try:
        amt = float(raw)
    except Exception:
        return safe_send(m.chat.id, f"{pe('cross')} Invalid amount. Use a number.")
    r = get_user(uid) or {}
    cur = float(r.get("balance", 0))
    if mode == "set":
        new = amt
    elif mode == "add":
        new = cur + amt
    else:
        new = max(0.0, cur - amt)
    set_balance(uid, new)
    safe_send(m.chat.id,
              f"{pe('check')} Balance updated for <code>{uid}</code>\n"
              f"{DIV}\n{pe('wallet')} New balance: <code>{money(new)}</code>",
              reply_markup=types.InlineKeyboardMarkup().add(
                  IB("\U0001f464 View User", callback_data=f"user_{uid}"),
                  IB("\u2b05 Users", callback_data="adm_users")))

@bot.callback_query_handler(func=lambda c: c.data.startswith("userban_"))
def cb_userban(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    uid = int(c.data.split("_")[1])
    r = get_user(uid) or {}
    newflag = 0 if r.get("banned") else 1
    set_banned(uid, newflag)
    bot.answer_callback_query(c.id, "Banned." if newflag else "Unbanned.")
    r = get_user(uid) or {}
    safe_edit(c.message.chat.id, c.message.message_id, _user_detail_text(r),
              _user_detail_kb(uid, r.get("banned")))

# ── Force join management ──────────────────────────────────────────────────
def _show_fj_menu(chat_id, mid=None):
    rows = get_force_join()
    lines = [f"{pe('link')} <b>Force Join Channels</b>", DIV]
    if not rows:
        lines.append(f"{pe('cross')} None set.")
    for r in rows:
        lines.append(f"{pe('check')} <b>{esc(r['name'])}</b>\n   {esc(r['link'])}")
    kb = types.InlineKeyboardMarkup()
    kb.add(IB("➕ Add Channel", callback_data="fj_add"))
    for r in rows:
        kb.add(IB(f"🗑 Remove {r['name']}", callback_data=f"fj_del_{r['id']}"))
    kb.add(IB("⬅ Back", callback_data="adm_menu"))
    txt = "\n".join(lines)
    if mid:
        safe_edit(chat_id, mid, txt, kb)
    else:
        safe_send(chat_id, txt, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "fj_add")
def cb_fj_add(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    bot.answer_callback_query(c.id)
    msg = safe_send(c.message.chat.id,
                    f"{pe('link')} <b>Add Force-Join — Step 1/2</b>\n{DIV}\n"
                    f"{pe('point')} Send the channel <b>link</b> (e.g. <code>https://t.me/YourChannel</code>)")
    bot.register_next_step_handler(msg, step_fj_link)

def step_fj_link(m):
    if not is_admin(m.from_user.id):
        return
    link = (m.text or "").strip()
    ADMIN_STATE[m.from_user.id] = {"step": "fj_name", "link": link}
    msg = safe_send(m.chat.id,
                    f"{pe('gem')} <b>Step 2/2</b>\n{DIV}\n"
                    f"{pe('point')} Send the <b>button name</b> to show (exact, premium emoji allowed):")
    bot.register_next_step_handler(msg, step_fj_name)

def step_fj_name(m):
    if not is_admin(m.from_user.id):
        return
    st = ADMIN_STATE.get(m.from_user.id, {})
    link = st.get("link")
    name = (m.text or "").strip()
    if not link or not name:
        return safe_send(m.chat.id, f"{pe('cross')} Invalid. Start again with /admin")
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("INSERT INTO force_join(link,name,added_at) VALUES(?,?,?)",
                  (link, name, datetime.utcnow().isoformat()))
        con.commit(); con.close()
    ADMIN_STATE.pop(m.from_user.id, None)
    safe_send(m.chat.id, f"{pe('check')} Force-join channel added: <b>{esc(name)}</b>",
              reply_markup=types.InlineKeyboardMarkup().add(
                  IB("⬅ Force Join Menu", callback_data="adm_fj")))

@bot.callback_query_handler(func=lambda c: c.data.startswith("fj_del_"))
def cb_fj_del(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    fid = int(c.data.split("_")[2])
    with _db_lock:
        con = db(); c2 = con.cursor()
        c2.execute("DELETE FROM force_join WHERE id=?", (fid,))
        con.commit(); con.close()
    bot.answer_callback_query(c.id, "Removed.")
    _show_fj_menu(c.message.chat.id, c.message.message_id)

# ── Settings ───────────────────────────────────────────────────────────────
def _show_settings(chat_id, mid):
    ref_on = str(get_setting("referral_enabled", "1")) != "0"
    qr_mode = get_setting("qr_mode", "static")
    qr_img = get_setting("qr_photo", "")
    txt = (f"{pe('target')} <b>Store Settings</b>\n{DIV}\n"
           f"{pe('bank')} UPI ID: <code>{esc(get_setting('upi_id'))}</code>\n"
           f"{pe('user')} UPI Name: <code>{esc(get_setting('upi_name'))}</code>\n"
           f"{pe('money')} Min Deposit: <code>{CURRENCY}{esc(get_setting('min_deposit'))}</code>\n"
           f"{pe('globe')} API Base URL: <code>{esc(get_api_base() or 'provisioning...')}</code>\n"
           f"{pe('key')} API ID: <code>{esc(get_setting('api_id') or 'built-in')}</code>\n"
           f"{pe('key')} API Hash: <code>{esc(get_setting('api_hash') or 'built-in')}</code>\n"
           f"{pe('globe')} MTProto Proxy: <code>{esc(get_setting('tl_proxy') or 'auto / direct')}</code>\n"
           f"{pe('gift')} Referral: <b>{'ON' if ref_on else 'OFF'}</b> • "
           f"<code>{money(get_setting('referral_reward', '1'))}</code> per refer\n"
           f"{pe('card')} QR Mode: <b>{esc(qr_mode)}</b> • "
           f"{'image set' if qr_img else 'no image'}\n"
           f"{DIV}\n{pe('point')} Tap to edit:")
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(IB("✏️ Edit UPI ID", callback_data="set_upi"),
           IB("✏️ Edit UPI Name", callback_data="set_upiname"),
           IB("✏️ Edit Min Deposit", callback_data="set_mindep"),
           IB("✏️ Edit API Base URL", callback_data="set_apiurl"),
           IB("🔑 Set API ID", callback_data="set_apiid"),
           IB("🔑 Set API Hash", callback_data="set_apihash"),
           IB("🌐 MTProto Proxy (fix OTP)", callback_data="set_tlproxy"),
           IB("🎁 Referral Reward", callback_data="set_refreward"),
           IB("🔁 Toggle Referral", callback_data="set_reftoggle"),
           IB("📷 Set QR Image", callback_data="set_qrimg"),
           IB("🔳 QR Mode", callback_data="set_qrmode"),
           IB("⬅ Back", callback_data="adm_menu"))
    safe_edit(chat_id, mid, txt, kb)

@bot.callback_query_handler(func=lambda c: c.data in (
    "set_upi", "set_upiname", "set_mindep", "set_apiurl", "set_apiid", "set_apihash",
    "set_refreward", "set_reftoggle", "set_qrimg", "set_qrmode", "set_tlproxy"))
def cb_settings_edit(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    data = c.data
    if data == "set_reftoggle":
        cur = str(get_setting("referral_enabled", "1"))
        set_setting("referral_enabled", "0" if cur != "0" else "1")
        bot.answer_callback_query(c.id, "Referral toggled.")
        _show_settings(c.message.chat.id, c.message.message_id)
        return
    if data == "set_qrmode":
        modes = ["off", "static", "auto"]
        cur = get_setting("qr_mode", "static")
        nxt = modes[(modes.index(cur) + 1) % len(modes)] if cur in modes else "static"
        set_setting("qr_mode", nxt)
        bot.answer_callback_query(c.id, "QR mode: %s" % nxt)
        _show_settings(c.message.chat.id, c.message.message_id)
        return
    bot.answer_callback_query(c.id)
    if data == "set_qrimg":
        msg = safe_send(c.message.chat.id,
                        f"{pe('card')} <b>Set Static QR Image</b>\n{DIV}\n"
                        f"{pe('point')} Send the QR code <b>photo</b> that every user should see.")
        bot.register_next_step_handler(msg, step_qr_photo)
        return
    prompt = {"set_upi": "Send the new UPI ID (e.g. name@bank)",
              "set_upiname": "Send the new UPI payee name",
              "set_mindep": "Send the new minimum deposit (number)",
              "set_apiurl": "Send the public API base URL (e.g. https://your-domain/api/v1)",
              "set_apiid": "Send your Telegram API ID (from my.telegram.org)",
              "set_apihash": "Send your Telegram API Hash (from my.telegram.org)",
              "set_refreward": "Send the new referral reward in rupees (e.g. 1 or 0.8)",
              "set_tlproxy": ("Send the proxy for Telegram MTProto, e.g. "
                              "socks5://user:pass@1.2.3.4:1080 or http://1.2.3.4:8080 "
                              "(send 'off' to disable)")}[data]
    msg = safe_send(c.message.chat.id, f"{pe('target')} {prompt}:")
    bot.register_next_step_handler(msg, step_setting, data)

def step_qr_photo(m):
    if not is_admin(m.from_user.id):
        return
    if not m.photo:
        return safe_send(m.chat.id, f"{pe('cross')} Please send a photo of the QR code.")
    fid = m.photo[-1].file_id
    set_setting("qr_photo", fid)
    set_setting("qr_mode", "static")
    safe_send(m.chat.id, f"{pe('check')} Static QR saved &amp; mode set to <b>static</b>.",
              reply_markup=types.InlineKeyboardMarkup().add(
                  IB("⬅ Settings", callback_data="adm_settings")))

def step_setting(m, which):
    if not is_admin(m.from_user.id):
        return
    val = (m.text or "").strip()
    key = {"set_upi": "upi_id", "set_upiname": "upi_name",
           "set_mindep": "min_deposit", "set_apiurl": "api_base_url",
           "set_apiid": "api_id", "set_apihash": "api_hash",
           "set_refreward": "referral_reward", "set_tlproxy": "tl_proxy"}[which]
    if key == "tl_proxy":
        if val.lower() in ("off", "none", "direct", "-"):
            val = ""
        _TL_PROXY_CACHE.update({"ready": False, "value": None, "ts": 0.0})
    set_setting(key, val)
    safe_send(m.chat.id, f"{pe('check')} Saved: <b>{esc(key)}</b> = <code>{esc(val)}</code>",
              reply_markup=types.InlineKeyboardMarkup().add(
                  IB("⬅ Settings", callback_data="adm_settings")))

# ── Deposits ───────────────────────────────────────────────────────────────
# ── API Accounts (Server 1) admin ───────────────────────────────────────────
def _show_api_admin(chat_id, mid):
    bal = tgshark_balance()
    if str(bal.get("status")) == "ok":
        bal_line = f"<code>{esc(bal.get('balance', '?'))}</code> {esc(bal.get('currency') or 'USD')}"
    else:
        bal_line = f"{pe('cross')} {esc(bal.get('message') or 'unavailable')}"
    prices = _api_country_prices()
    on = str(get_setting("api_enabled", "1")) != "0"
    txt = (f"{pe('laptop')} <b>API Accounts \u2022 Server 1</b>\n{DIV}\n"
           f"{pe('bank')} <b>API Balance:</b> {bal_line}\n"
           f"{pe('gear')} <b>Status:</b> <b>{'ON' if on else 'OFF'}</b>\n"
           f"{pe('money')} <b>Default price:</b> <code>{money(get_setting('api_price', '0'))}</code>\n"
           f"{pe('chart')} <b>USD rate:</b> <code>{esc(get_setting('api_usd_rate', '90'))}</code> "
           f"\u2022 <b>Profit:</b> <code>{esc(get_setting('api_profit', '0'))}</code>\n"
           f"{pe('tag')} <b>Country overrides:</b> <b>{len(prices)}</b> set\n"
           f"{DIV}\n"
           f"{pe('info')} <i>If default = 0, price = API price (USD \u00d7 rate) + profit. "
           f"Profit 1 = 100% (\u20b910 \u2192 \u20b920), 0.5 = 50%, 0.1 = 10%.</i>\n"
           f"{pe('point')} Tap to configure:")
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(IB("\U0001F4B0 Set Default Price (\u20b9)", callback_data="set_apiprice", style=BTN_SUCCESS))
    kb.add(IB("\U0001F4B5 Set USD Rate (\u20b9 per $)", callback_data="set_apirate", style=BTN_PRIMARY))
    kb.add(IB("\U0001F4B8 Set Profit", callback_data="set_apiprofit", style=BTN_SUCCESS))
    kb.add(IB("\U0001F3F7 Set Country Price", callback_data="set_apicountry", style=BTN_SUCCESS))
    kb.add(IB("\U0001F504 Refresh API Balance", callback_data="adm_api", style=BTN_PRIMARY))
    kb.add(IB("\u26D4 Disable API Server" if on else "\u2705 Enable API Server",
              callback_data="api_toggle", style=BTN_DANGER if on else BTN_SUCCESS))
    kb.add(IB("\u2b05 Back", callback_data="adm_menu", style=BTN_DANGER))
    safe_edit(chat_id, mid, txt, kb)

@bot.callback_query_handler(func=lambda c: c.data in (
    "set_apiprice", "set_apirate", "set_apiprofit", "set_apicountry", "api_toggle"))
def cb_api_admin(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    data = c.data
    if data == "api_toggle":
        cur = str(get_setting("api_enabled", "1"))
        set_setting("api_enabled", "0" if cur != "0" else "1")
        bot.answer_callback_query(c.id, "API server toggled.")
        _show_api_admin(c.message.chat.id, c.message.message_id)
        return
    bot.answer_callback_query(c.id)
    prompts = {
        "set_apiprice": ("Send the <b>default price</b> in \u20b9 for ALL API accounts "
                         "(send <code>0</code> to use auto-conversion):", "api_price"),
        "set_apirate": ("Send the <b>USD \u2192 \u20b9 rate</b> (e.g. <code>90</code>):", "api_usd_rate"),
        "set_apiprofit": ("Send the <b>profit</b> multiplier: <code>1</code> = 100% (\u20b910 \u2192 \u20b920), "
                          "<code>0.5</code> = 50%, <code>0.1</code> = 10%, <code>0</code> = none:", "api_profit"),
    }
    if data == "set_apicountry":
        msg = safe_send(c.message.chat.id,
                        f"{pe('tag')} <b>Set Country Price</b>\n{DIV}\n"
                        f"{pe('point')} Send the <b>country code</b> (the number from the API list, "
                        f"e.g. <code>49</code> for USA, <code>2</code> for India):")
        bot.register_next_step_handler(msg, step_api_country_code)
        return
    prompt, key = prompts[data]
    msg = safe_send(c.message.chat.id, f"{pe('target')} {prompt}")
    bot.register_next_step_handler(msg, step_api_setting, key)

def step_api_setting(m, key):
    if not is_admin(m.from_user.id):
        return
    val = (m.text or "").strip()
    try:
        float(val)
    except Exception:
        return safe_send(m.chat.id, f"{pe('cross')} Please send a valid number.")
    set_setting(key, val)
    safe_send(m.chat.id, f"{pe('check')} Saved: <b>{esc(key)}</b> = <code>{esc(val)}</code>",
              reply_markup=types.InlineKeyboardMarkup().add(
                  IB("\U0001F50C API Panel", callback_data="adm_api", style=BTN_PRIMARY)))

def step_api_country_code(m):
    if not is_admin(m.from_user.id):
        return
    code = re.sub(r'\D', '', m.text or "")
    if not code:
        return safe_send(m.chat.id, f"{pe('cross')} Invalid country code.")
    ADMIN_STATE[m.from_user.id] = {"step": "api_country_price", "country": code}
    msg = safe_send(m.chat.id,
                    f"{pe('money')} <b>Country {esc(code)}</b>\n{DIV}\n"
                    f"{pe('point')} Send the price in \u20b9 for this country "
                    f"(send <code>0</code> to remove the override):")
    bot.register_next_step_handler(msg, step_api_country_price)

def step_api_country_price(m):
    if not is_admin(m.from_user.id):
        return
    st = ADMIN_STATE.get(m.from_user.id, {})
    code = st.get("country")
    if not code:
        return
    try:
        price = float(re.sub(r'[^\d.]', '', m.text or ""))
    except Exception:
        return safe_send(m.chat.id, f"{pe('cross')} Invalid price.")
    prices = _api_country_prices()
    if price <= 0:
        prices.pop(code, None)
        note = "removed"
    else:
        prices[code] = price
        note = f"set to {money(price)}"
    set_setting("api_country_prices", json.dumps(prices))
    ADMIN_STATE.pop(m.from_user.id, None)
    safe_send(m.chat.id, f"{pe('check')} Country <b>{esc(code)}</b> price {note}.",
              reply_markup=types.InlineKeyboardMarkup().add(
                  IB("\U0001F50C API Panel", callback_data="adm_api", style=BTN_PRIMARY)))

def _show_deposits(chat_id, mid):
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("SELECT * FROM deposits ORDER BY id DESC LIMIT 15")
        rows = [dict(r) for r in c.fetchall()]; con.close()
    lines = [f"{pe('money')} <b>Recent Deposits</b>", DIV]
    if not rows:
        lines.append(f"{pe('cross')} None.")
    for r in rows:
        lines.append(f"#{r['id']} • <code>{r['user_id']}</code> • {money(r['amount'])} • "
                     f"{r['status']} • UTR {esc(r['utr'])}")
    kb = types.InlineKeyboardMarkup().add(
        IB("⬅ Back", callback_data="adm_menu"))
    safe_edit(chat_id, mid, "\n".join(lines), kb)

# ── Broadcast ──────────────────────────────────────────────────────────────
def step_broadcast(m):
    if not is_admin(m.from_user.id):
        return
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("SELECT user_id FROM users WHERE banned=0")
        uids = [r["user_id"] for r in c.fetchall()]; con.close()
    ok = fail = 0
    for uid in uids:
        try:
            bot.copy_message(uid, m.chat.id, m.message_id)
            ok += 1
            time.sleep(0.05)
        except Exception:
            fail += 1
    safe_send(m.chat.id, f"{pe('check')} <b>Broadcast done</b>\n{DIV}\n"
                         f"{pe('bolt')} Sent: {ok} • Failed: {fail}")

# ── Emoji capture ──────────────────────────────────────────────────────────
def step_capture_emoji(m):
    if not is_admin(m.from_user.id):
        return
    txt = m.text or ""
    m2 = re.match(r'(flag\s+\w+|\w+)\s*=\s*(.+)', txt)
    if m2:
        key, char = m2.group(1).strip(), m2.group(2).strip()
    else:
        key, char = "custom", txt.strip()
    eid = None
    for e in (m.entities or []):
        if e.type == "custom_emoji":
            eid = e.custom_emoji_id
            break
    if not eid:
        return safe_send(m.chat.id, f"{pe('cross')} No premium emoji found. Send it from a Premium account "
                                    f"as <code>key=</code> + the emoji (or <code>flag IN=</code> + the flag).")
    if key.lower().startswith("flag"):
        cc = key.split()[-1].upper()
        save_flag(cc, eid)
        safe_send(m.chat.id, f"{pe('check')} Captured flag <b>{esc(cc)}</b> {pe('arrow')} ID <code>{eid}</code>")
    else:
        save_emoji(key, char, eid)
        safe_send(m.chat.id, f"{pe('check')} Captured emoji <b>{esc(key)}</b> {pe('arrow')} ID <code>{eid}</code>")

def step_menu_photo(m):
    if not is_admin(m.from_user.id):
        return
    if not m.photo:
        return safe_send(m.chat.id, f"{pe('cross')} Please send a photo.")
    fid = m.photo[-1].file_id
    set_setting("menu_photo", fid)
    safe_send(m.chat.id, f"{pe('check')} Menu photo updated!",
              reply_markup=types.InlineKeyboardMarkup().add(
                  IB("⬅ Back", callback_data="adm_menu")))

# ── RAW CAPTURE (owner sends premium emojis / photos anytime) ──────────────────────
CAPTURE_FILE = "captured_data.json"

def _log_capture(kind, value):
    try:
        data = _load_json(CAPTURE_FILE, {}) or {}
        data.setdefault(kind, [])
        if value not in data[kind]:
            data[kind].append(value)
        _save_json(CAPTURE_FILE, data)
    except Exception as e:
        log.warning("capture log: %s", e)

def _has_custom_emoji(m):
    return any(getattr(e, "type", "") == "custom_emoji" for e in (m.entities or []))

@bot.message_handler(content_types=["photo"], func=lambda m: is_admin(m.from_user.id))
def owner_photo_capture(m):
    """Any photo the owner sends is saved as the menu banner + logged."""
    try:
        fid = m.photo[-1].file_id
        _log_capture("photos", fid)
        try:
            fi = bot.get_file(fid)
            content = bot.download_file(fi.file_path)
            os.makedirs("inputs", exist_ok=True)
            fn = os.path.join("inputs", "owner_photo_%d.jpg" % int(time.time()))
            with open(fn, "wb") as f:
                f.write(content)
            _log_capture("photo_files", fn)
        except Exception as e:
            log.warning("photo download: %s", e)
        set_setting("menu_photo", fid)
        safe_send(m.chat.id,
                  f"{pe('check')} Photo received & saved as menu banner.\n"
                  f"{pe('id')} file_id: <code>{fid}</code>")
    except Exception as e:
        log.warning("photo capture: %s", e)

@bot.message_handler(content_types=["text"],
                     func=lambda m: is_admin(m.from_user.id) and _has_custom_emoji(m))
def owner_emoji_capture(m):
    """Any message with premium emoji(s) from the owner is logged (IDs saved)."""
    try:
        ids = [e.custom_emoji_id for e in (m.entities or []) if getattr(e, "type", "") == "custom_emoji"]
        for i in ids:
            _log_capture("emoji_ids", i)
        chars = m.text or ""
        _log_capture("raw_texts", chars)
        safe_send(m.chat.id,
                  f"{pe('check')} Captured <b>{len(ids)}</b> premium emoji ID(s):\n"
                  f"<code>{', '.join(ids)}</code>")
    except Exception as e:
        log.warning("emoji capture: %s", e)

@bot.message_handler(content_types=["text"],
                     func=lambda m: is_admin(m.from_user.id) and bool(re.fullmatch(r"\d{15,20}", (m.text or "").strip())))
def owner_id_capture(m):
    """Owner pastes a raw emoji ID (15-20 digits) -> logged."""
    try:
        _log_capture("emoji_ids", m.text.strip())
        safe_send(m.chat.id, f"{pe('check')} Emoji ID logged: <code>{m.text.strip()}</code>")
    except Exception as e:
        log.warning("id capture: %s", e)

# ═══════════════════════════════════════════════════════════════════════════
#  ADD ACCOUNT  (Telethon login flow)
# ═══════════════════════════════════════════════════════════════════════════
_TL_CLIENTS = {}   # uid -> TelegramClient (during login)

# ── Shared Telethon event loop ──────────────────────────────────────────────
# Telethon clients are bound to ONE event loop. Creating a loop in one thread
# and reusing it from another thread breaks sign_in (the coroutine is never
# awaited -> "Login failed"). We run every login coroutine on a single
# persistent loop living in its own daemon thread, submitted thread-safely.
_TL_LOOP = None
_TL_LOOP_LOCK = threading.Lock()

def _tl_loop():
    global _TL_LOOP
    with _TL_LOOP_LOCK:
        if _TL_LOOP is None or _TL_LOOP.is_closed():
            _TL_LOOP = asyncio.new_event_loop()
            _t = threading.Thread(target=_TL_LOOP.run_forever, daemon=True)
            _t.start()
    return _TL_LOOP

def _tl_run(coro):
    """Run a coroutine on the shared Telethon loop (thread-safe) and block for its result."""
    return asyncio.run_coroutine_threadsafe(coro, _tl_loop()).result()

# ── MTProto network / proxy handling ──────────────────────────────────────────
# On many VPS / cloud / datacenter networks Telegram's raw MTProto TCP port is
# filtered: the TCP handshake succeeds but the socket is closed immediately,
# which Telethon reports as
#     IncompleteReadError: 0 bytes read on a total of 8 expected bytes
# (or a bare "ConnectionError"). Routing Telethon through a SOCKS5 / HTTP proxy
# fixes it. Resolution order:
#   1. Admin setting  `tl_proxy`   e.g. socks5://user:pass@host:1080
#   2. Env var        TL_PROXY / TELEGRAM_PROXY
#   3. Auto-detect a local tunnel (psiphon / tor / ssh -D) on common ports
_TL_PROXY_CACHE = {"ready": False, "value": None, "ts": 0.0}

def _tl_proxy():
    """Return a Telethon-compatible proxy tuple, or None for a direct connection."""
    now = time.time()
    if _TL_PROXY_CACHE["ready"] and (now - _TL_PROXY_CACHE["ts"]) < 60:
        return _TL_PROXY_CACHE["value"]

    proxy = None
    raw = ""
    try:
        raw = str(get_setting("tl_proxy") or "").strip()
    except Exception:
        raw = ""
    if not raw:
        raw = (os.environ.get("TL_PROXY")
               or os.environ.get("TELEGRAM_PROXY")
               or os.environ.get("HTTPS_PROXY")
               or "").strip()
    if raw and raw.lower().startswith(("http://", "https://", "socks4://", "socks5://", "socks5h://")):
        try:
            from urllib.parse import urlparse as _up
            u = _up(raw)
            scheme = (u.scheme or "http").lower()
            kind = "socks5" if scheme.startswith("socks5") else ("socks4" if scheme.startswith("socks4") else "http")
            host = u.hostname
            port = u.port or (1080 if kind.startswith("socks") else 8080)
            if host:
                if u.username:
                    proxy = (kind, host, int(port), True, u.username, u.password or "")
                else:
                    proxy = (kind, host, int(port))
                log.info("Telethon proxy (configured): %s://%s:%s", kind, host, port)
        except Exception as e:
            log.warning("bad tl_proxy %r: %s", raw, e)

    if proxy is None:
        for host, port, kind in (("127.0.0.1", 18080, "http"),
                                 ("127.0.0.1", 8080, "http"),
                                 ("127.0.0.1", 1080, "socks5"),
                                 ("127.0.0.1", 9050, "socks5")):
            try:
                _s = socket.create_connection((host, port), timeout=1.5)
                _s.close()
                proxy = (kind, host, port)
                log.info("Telethon proxy (auto-detected): %s://%s:%s", kind, host, port)
                break
            except Exception:
                continue

    _TL_PROXY_CACHE.update({"ready": True, "value": proxy, "ts": now})
    return proxy


def _tl_client(session, api_id, api_hash, **kw):
    """Build a TelegramClient with proxy + resilient retry settings.

    Replaces every bare `TelegramClient(StringSession(...), id, hash)` call so
    that login / OTP / session use all benefit from the same network config.
    """
    opts = dict(connection_retries=5, retry_delay=2, timeout=30,
                request_retries=5, auto_reconnect=True, sequential_updates=False)
    opts.update(kw)
    proxy = _tl_proxy()
    if proxy:
        opts["proxy"] = proxy
    try:
        return TelegramClient(StringSession(session or ""), int(api_id), api_hash, **opts)
    except TypeError:
        # older Telethon without python-socks -> drop proxy, keep retries
        opts.pop("proxy", None)
        return TelegramClient(StringSession(session or ""), int(api_id), api_hash, **opts)


def _tl_friendly_error(e):
    """Turn low-level Telethon/network failures into an actionable hint."""
    msg = str(e)
    low = msg.lower()
    if ("0 bytes read" in low or "incomplete" in low or "connection" in low
            or "timeout" in low or "not connected" in low or "network" in low):
        return ("\n" + DIV + "\n" + pe("warn") + " <b>Server network blocked Telegram MTProto.</b>\n"
                + pe("point") + " This is a <b>server-side network</b> problem, not your number.\n"
                + pe("point") + " Fix: set a proxy in <b>Admin \u2192 Settings \u2192 MTProto Proxy</b> "
                  "(<code>socks5://user:pass@host:port</code>) or use "
                  "<b>Login via QR Code</b>.")
    if "recaptcha" in low:
        return ("\n" + DIV + "\n" + pe("warn") + " <b>Telegram requires a reCAPTCHA.</b>\n"
                + pe("point") + " Use your own <b>API ID / Hash</b> from my.telegram.org "
                  "(Admin \u2192 Settings) \u2014 shared API IDs trigger this.")
    if "flood" in low:
        return ("\n<i>Telegram rate-limited this number. Wait a few minutes "
                "or try another number.</i>")
    if "api_id" in low or "api id" in low or "api_hash" in low:
        return ("\n<i>Set your own API ID / Hash in Admin \u2192 Settings "
                "(from my.telegram.org).</i>")
    if "phone_number_invalid" in low or "invalid" in low and "phone" in low:
        return ("\n<i>Check the number: it must include the country code "
                "(e.g. <code>+9198xxxxxxxx</code>).</i>")
    return ""

def _resend_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(IB("AUTO-READ CODE FROM PHONE (ADB)", callback_data="acc_autootp",
              style=BTN_SUCCESS, icon=EID("phone")))
    kb.add(IB("RESEND CODE (Telegram App)", callback_data="acc_resend",
              style=BTN_PRIMARY, icon=EID("refresh")))
    kb.add(IB("RESEND VIA SMS", callback_data="acc_resend_sms",
              style=BTN_PRIMARY, icon=EID("phone")))
    kb.add(IB("RESEND VIA PHONE CALL", callback_data="acc_resend_call",
              style=BTN_PRIMARY, icon=EID("phone")))
    kb.add(IB("LOGIN VIA QR CODE (Always Works)", callback_data="acc_m_qr",
              style=BTN_SUCCESS, icon=EID("camera")))
    return kb

@bot.callback_query_handler(func=lambda c: c.data == "acc_autootp")
def cb_acc_autootp(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    if not _phone_ready():
        bot.answer_callback_query(c.id, "No Android phone connected.", show_alert=True)
        safe_send(c.message.chat.id,
                  f"{pe('cross')} <b>Phone VPS not ready</b> \u2014 {esc(_phone_status_text())}.")
        return
    bot.answer_callback_query(c.id, "Reading code from phone\u2026")
    threading.Thread(target=_phone_auto_otp,
                     args=(c.from_user.id, c.message.chat.id), daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data in ("acc_resend", "acc_resend_call", "acc_resend_sms"))
def cb_acc_resend(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    st = ADMIN_STATE.get(c.from_user.id, {})
    phone = st.get("phone")
    if not phone:
        bot.answer_callback_query(c.id, "Start again with /admin", show_alert=True); return
    method = {"acc_resend_call": "call", "acc_resend_sms": "sms"}.get(c.data, "app")
    bot.answer_callback_query(c.id, "Resending code\u2026")
    _acc_resend(c.from_user.id, c.message.chat.id, phone, method)

def _acc_resend(uid, chat_id, phone, method="app"):
    """Resend the login code.

    method: 'app'  -> normal resend (delivered to the official Telegram app)
            'sms'  -> force a fresh SendCodeRequest that prefers SMS
            'call' -> request a phone-call / flash-call delivery
    """
    if not TELETHON_OK:
        return safe_send(chat_id, f"{pe('cross')} Telethon not installed on server.")
    st = ADMIN_STATE.get(uid, {})
    hash_ = st.get("phone_code_hash")
    cl = _TL_CLIENTS.get(uid)
    if not cl or not hash_:
        return _acc_send_code(uid, chat_id, phone, method)
    api_id = st.get("api_id") or get_setting("api_id") or str(DEFAULT_API_ID)
    api_hash = st.get("api_hash") or get_setting("api_hash") or DEFAULT_API_HASH
    safe_send(chat_id, f"{pe('clock')} Requesting a new code\u2026")
    def _do():
        try:
            async def _inner():
                if method == "call":
                    settings = types.CodeSettings(allow_flashcall=True,
                                                  current_number=False,
                                                  allow_app_hash=False,
                                                  allow_missed_call=True)
                    try:
                        return await cl(functions.auth.ResendCodeRequest(phone, hash_))
                    except Exception:
                        return await cl(functions.auth.SendCodeRequest(
                            phone, int(api_id), api_hash, settings))
                if method == "sms":
                    settings = types.CodeSettings(allow_flashcall=False,
                                                  current_number=True,
                                                  allow_app_hash=False,
                                                  allow_missed_call=False)
                    try:
                        return await cl(functions.auth.ResendCodeRequest(phone, hash_))
                    except Exception:
                        return await cl(functions.auth.SendCodeRequest(
                            phone, int(api_id), api_hash, settings))
                return await cl.send_code_request(phone)
            sent = _tl_run(_inner())
            if sent is not None and getattr(sent, "phone_code_hash", None):
                st["phone_code_hash"] = sent.phone_code_hash
            tname = type(getattr(sent, "type", None)).__name__ if sent else ""
            if "Sms" in tname or "Fragment" in tname:
                where = "the <b>SMS inbox</b> of that number"
            elif "FlashCall" in tname:
                where = "an incoming <b>flash call</b> (last digits = code)"
            elif "Call" in tname:
                where = "an incoming <b>phone call</b> (the code is read out)"
            else:
                where = ("the <b>official Telegram app</b> on that number \u2014 chat "
                         "<b>Telegram</b> (id <code>777000</code>)")
            msg = safe_send(chat_id, f"{pe('check')} <b>New code requested.</b>\n{DIV}\n"
                               f"{pe('point')} Telegram delivered it to {where}.\n"
                               f"{pe('chart')} <b>Telegram reply:</b> <code>{tname or 'unknown'}</code>\n"
                               f"{pe('point')} Send that <b>OTP</b> here:")
            bot.register_next_step_handler(msg, step_acc_code)
        except Exception as e:
            safe_send(chat_id, f"{pe('cross')} Resend failed: <code>{esc(e)}</code>")
    threading.Thread(target=_do, daemon=True).start()

def _acc_send_qr(uid, chat_id, phone):
    """Log in by scanning a QR with the official Telegram app.

    This BYPASSES code delivery entirely (Telegram only delivers login codes to
    the official app), so it is the most reliable way to add an account."""
    if not TELETHON_OK:
        return safe_send(chat_id, f"{pe('cross')} Telethon not installed on server.")
    if not QR_OK:
        return safe_send(chat_id, f"{pe('cross')} QR library not installed on server.")
    st = ADMIN_STATE.get(uid, {})
    api_id = st.get("api_id") or get_setting("api_id") or str(DEFAULT_API_ID)
    api_hash = st.get("api_hash") or get_setting("api_hash") or DEFAULT_API_HASH
    safe_send(chat_id, f"{pe('clock')} Generating a QR login code\u2026")

    def _cap():
        return (f"\U0001F510 <b>QR Login \u2014 Scan to add this account</b>\n{DIV}\n"
                f"\U0001F4F1 Number: <code>{esc(phone)}</code>\n"
                f"\U0001F449 <b>1.</b> Open the <b>official Telegram app</b> on that "
                f"number.\n"
                f"\U0001F449 <b>2.</b> <b>Settings</b> \u2192 <b>Devices</b> \u2192 "
                f"<b>Link Desktop Device</b>.\n"
                f"\U0001F449 <b>3.</b> Scan the QR below.\n"
                f"\u2139\ufe0f The QR auto-refreshes \u2014 always scan the <b>latest</b> image.")

    def _send_qr(qr, state):
        path = os.path.join(BASE_DIR, f"qr_login_{uid}.png")
        try:
            qrcode.make(qr.url).save(path)
        except Exception as e:
            log.warning("qr image failed: %s", e)
            return state
        cap = _cap()
        if state.get("mid"):
            try:
                with open(path, "rb") as f:
                    bot.edit_message_media(
                        types.InputMediaPhoto(f, caption=cap, parse_mode="HTML"),
                        chat_id, state["mid"])
                return state
            except Exception as e:
                log.warning("qr edit failed: %s", e)
        try:
            with open(path, "rb") as f:
                m = bot.send_photo(chat_id, f, caption=cap, parse_mode="HTML")
            state["mid"] = getattr(m, "message_id", None)
        except Exception as e:
            log.warning("qr send failed: %s", e)
            safe_send(chat_id, cap)
        return state

    def _do():
        try:
            async def _inner():
                cl = _tl_client("", api_id, api_hash)
                await cl.connect()
                qr = await cl.qr_login()
                return cl, qr
            cl, qr = _tl_run(_inner())
            _TL_CLIENTS[uid] = cl
            st["step"] = "acc_qr"
            state = _send_qr(qr, {})
            deadline = time.time() + 300
            while time.time() < deadline:
                try:
                    _tl_run(qr.wait(timeout=30))
                    _after_login(uid, chat_id)
                    return
                except asyncio.TimeoutError:
                    try:
                        _tl_run(qr.recreate())
                        state = _send_qr(qr, state)
                    except Exception as e:
                        log.warning("qr recreate failed: %s", e)
                        break
                except SessionPasswordNeededError:
                    st["step"] = "acc_2fa"
                    msg = safe_send(chat_id, f"{pe('lock')} <b>2FA Required</b>\n{DIV}\n"
                                             f"{pe('point')} Send your <b>2FA password</b>:")
                    bot.register_next_step_handler(msg, step_acc_2fa)
                    return
                except Exception as e:
                    safe_send(chat_id, f"{pe('cross')} QR login error: <code>{esc(e)}</code>")
                    return
            safe_send(chat_id, f"{pe('clock')} QR expired. Tap <b>Login via QR Code</b> "
                               f"again for a fresh code.")
        except Exception as e:
            safe_send(chat_id, f"{pe('cross')} QR login failed: <code>{esc(e)}</code>")
    threading.Thread(target=_do, daemon=True).start()

import urllib.request as _urlreq
import urllib.parse as _urlparse

WA_BRIDGE = os.environ.get("WA_BRIDGE_URL", "http://127.0.0.1:8790").rstrip("/")

def _wa_req(path, data=None, timeout=45):
    url = WA_BRIDGE + path
    try:
        if data is not None:
            body = json.dumps(data).encode()
            req = _urlreq.Request(url, data=body, headers={"Content-Type": "application/json"})
        else:
            req = _urlreq.Request(url)
        with _urlreq.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except Exception as e:
        return {"ok": False, "error": str(e)}

def _wa_pair(phone, sid, fresh=False):
    return _wa_req("/pair", {"phone": phone, "id": sid, "fresh": bool(fresh)}, timeout=50)

def _wa_status(sid):
    return _wa_req("/status?id=" + _urlparse.quote(str(sid)), timeout=15)

def _wa_logout(sid):
    return _wa_req("/logout", {"id": sid}, timeout=15)

# ── Phone VPS bridge helpers ────────────────────────────────────────────────
def _phone():
    """Return a PhoneBridge instance (or None if the module is unavailable)."""
    if not PHONE_BRIDGE_OK:
        return None
    try:
        return PhoneBridge()
    except Exception:
        return None

def _phone_ready():
    pb = _phone()
    return bool(pb and pb.ok())

def _phone_status_text():
    if not PHONE_BRIDGE_OK:
        return "not installed"
    pb = _phone()
    if not pb or not pb.adb_available():
        return "adb missing"
    devs = pb.online_devices()
    if not devs:
        return "no device connected"
    d = devs[0]
    return f"online \u2022 {d.get('model','device')}"

def _phone_auto_otp(uid, chat_id, package=None, tries=20, delay=6):
    """Background: poll the phone for the login code and auto-submit it."""
    pb = _phone()
    if not pb or not pb.ok():
        safe_send(chat_id, f"{pe('cross')} No Android phone connected to read the code.")
        return
    st = ADMIN_STATE.get(uid, {})
    safe_send(chat_id, f"{pe('phone')} Reading the code from the phone\u2026 "
                       f"(keep the phone unlocked)")
    code = None
    for _ in range(max(1, tries)):
        code = pb.read_otp(package) or pb.read_sms_otp(retries=1, delay=0)
        if code:
            break
        time.sleep(delay)
    if not code:
        safe_send(chat_id, f"{pe('cross')} Could not read a code yet. "
                           f"Enter it manually or tap Auto-read again.")
        return
    safe_send(chat_id, f"{pe('check')} Code <code>{esc(code)}</code> read from the phone \u2014 logging in\u2026")
    entry = _TL_CLIENTS.get(uid)
    if not entry:
        return safe_send(chat_id, f"{pe('cross')} Session lost. Start again with /admin")
    cl = entry
    def _do():
        try:
            async def _inner():
                await cl.sign_in(phone=st["phone"], code=code,
                                 phone_code_hash=st.get("phone_code_hash"))
            _tl_run(_inner())
            _after_login(uid, chat_id)
        except SessionPasswordNeededError:
            st["step"] = "acc_2fa"
            msg = safe_send(chat_id, f"{pe('lock')} <b>2FA Required</b>\n{DIV}\n"
                                     f"{pe('point')} Send your <b>2FA password</b>:")
            bot.register_next_step_handler(msg, step_acc_2fa)
        except Exception as e:
            safe_send(chat_id, f"{pe('cross')} Login failed: <code>{esc(e)}</code>")
    threading.Thread(target=_do, daemon=True).start()


def _show_addacc_platform(chat_id, mid=None):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(IB("\u2708\ufe0f Add Telegram Account", callback_data="acc_plat_telegram",
              style=BTN_SUCCESS, icon=EID("send")))
    kb.add(IB("\U0001F4AC Add WhatsApp Account", callback_data="acc_plat_whatsapp",
              style=BTN_SUCCESS, icon=EID("phone")))
    kb.add(IB("\U0001F4E5 Import Session (from a real phone)", callback_data="acc_plat_import",
              style=BTN_PRIMARY, icon=EID("key")))
    kb.add(IB("\u2b05 Back", callback_data="adm_menu", style=BTN_DANGER))
    txt = (f"{pe('box')} <b>Add Account \u2014 Choose Platform</b>\n{DIV}\n"
           f"{pe('point')} Which platform is this account for?\n"
           f"{pe('info')} Telegram and WhatsApp accounts are stored separately.\n"
           f"{pe('info')} <b>Import Session</b> \u2014 paste a Telegram session string "
           f"exported from a real phone (no OTP needed).")
    if mid:
        safe_edit(chat_id, mid, txt, kb)
    else:
        safe_send(chat_id, txt, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data in ("acc_plat_telegram", "acc_plat_whatsapp", "acc_plat_import"))
def cb_acc_platform(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    if c.data == "acc_plat_import":
        ADMIN_STATE[c.from_user.id] = {"step": "acc_import_phone", "platform": "telegram"}
        bot.answer_callback_query(c.id, "Import session")
        msg = safe_send(c.message.chat.id,
                        f"{pe('key')} <b>Import Telegram Session</b>\n{DIV}\n"
                        f"{pe('point')} Send the account's <b>phone number</b> "
                        f"(with country code, e.g. <code>+9198XXXXXXXX</code>):")
        bot.register_next_step_handler(msg, step_acc_import_phone)
        return
    platform = "whatsapp" if c.data.endswith("whatsapp") else "telegram"
    ADMIN_STATE[c.from_user.id] = {"step": "acc_server", "platform": platform}
    bot.answer_callback_query(c.id, PLATFORM_LABEL[platform] + " selected")
    _show_addacc_server(c.message.chat.id, c.message.message_id, platform)

def step_acc_import_phone(m):
    if not is_admin(m.from_user.id):
        return
    st = ADMIN_STATE.get(m.from_user.id, {})
    st["phone"] = (m.text or "").strip()
    st["step"] = "acc_import_session"
    msg = safe_send(m.chat.id,
                    f"{pe('key')} <b>Step 2/2 \u2014 Session String</b>\n{DIV}\n"
                    f"{pe('point')} Paste the <b>StringSession</b> for this number.\n"
                    f"{pe('info')} Export it from a real phone with any session exporter, "
                    f"or from another Telethon instance (<code>client.session.save()</code>).\n"
                    f"{pe('info')} Optional: send <code>api_id:api_hash:session</code> to "
                    f"use custom API credentials.")
    bot.register_next_step_handler(msg, step_acc_import_session)

def step_acc_import_session(m):
    if not is_admin(m.from_user.id):
        return
    st = ADMIN_STATE.get(m.from_user.id, {})
    raw = (m.text or "").strip()
    api_id = st.get("api_id") or get_setting("api_id") or str(DEFAULT_API_ID)
    api_hash = st.get("api_hash") or get_setting("api_hash") or DEFAULT_API_HASH
    session = raw
    # allow "api_id:api_hash:session"
    parts = raw.split(":", 2)
    if len(parts) == 3 and parts[0].isdigit() and len(parts[1]) == 32:
        api_id, api_hash, session = parts
    if not TELETHON_OK:
        return safe_send(m.chat.id, f"{pe('cross')} Telethon not installed on server.")
    safe_send(m.chat.id, f"{pe('clock')} Verifying the session\u2026")
    def _do():
        try:
            async def _inner():
                cl = _tl_client(session, api_id, api_hash)
                await cl.connect()
                if not await cl.is_user_authorized():
                    try: await cl.disconnect()
                    except Exception: pass
                    return None, "session is not authorized"
                me = await cl.get_me()
                try: await cl.disconnect()
                except Exception: pass
                return me, None
            me, err = _tl_run(_inner())
            if err or me is None:
                return safe_send(m.chat.id, f"{pe('cross')} Import failed: {esc(err or 'invalid session')}")
            st["session"] = session
            st["api_id"] = str(api_id)
            st["api_hash"] = str(api_hash)
            st["platform"] = "telegram"
            st["step"] = "acc_country"
            name = (getattr(me, "first_name", "") or "") + " " + (getattr(me, "last_name", "") or "")
            msg = safe_send(m.chat.id,
                            f"{pe('check')} <b>Session valid!</b> \u2022 {esc(name.strip() or 'account')} "
                            f"(id <code>{getattr(me,'id','')}</code>)\n{DIV}\n"
                            f"{pe('globe')} Which <b>country</b> is this account from? (e.g. India)")
            bot.register_next_step_handler(msg, step_acc_country)
        except Exception as e:
            safe_send(m.chat.id, f"{pe('cross')} Import failed: <code>{esc(e)}</code>")
    threading.Thread(target=_do, daemon=True).start()



def _wa_show_method(chat_id, uid):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(IB("\U0001F4F2 Send WhatsApp Pairing Code", callback_data="acc_wa_pair",
              style=BTN_SUCCESS, icon=EID("phone")))
    kb.add(IB("\u2b05 Cancel", callback_data="adm_menu", style=BTN_DANGER))
    safe_send(chat_id,
              f"{pe('lock')} <b>Step 2/6 \u2014 WhatsApp Login</b>\n{DIV}\n"
              f"{pe('info')} The bot will generate an <b>8-digit pairing code</b>.\n"
              f"{pe('point')} Enter it on the phone: <b>WhatsApp \u2192 Settings \u2192 "
              f"Linked Devices \u2192 Link a Device \u2192 Link with phone number instead</b>.",
              reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "acc_wa_pair")
def cb_wa_pair(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    st = ADMIN_STATE.get(c.from_user.id, {})
    phone = st.get("phone")
    if not phone:
        bot.answer_callback_query(c.id, "Start again with /admin", show_alert=True); return
    sid = "acc_" + re.sub(r'\D', '', phone)
    st["wa_sid"] = sid
    bot.answer_callback_query(c.id, "Generating code\u2026")
    safe_send(c.message.chat.id, f"{pe('clock')} Requesting WhatsApp pairing code\u2026")
    def _do():
        r = _wa_pair(phone, sid, fresh=True)
        if r.get("connected"):
            _wa_after_login(c.from_user.id, c.message.chat.id); return
        code = r.get("code")
        if not code:
            safe_send(c.message.chat.id,
                      f"{pe('cross')} <b>Could not get a WhatsApp code.</b>\n{DIV}\n"
                      f"<code>{esc(r.get('error'))}</code>\n"
                      f"{pe('info')} Make sure the WhatsApp bridge is running.")
            return
        pretty = "-".join(code[i:i+4] for i in range(0, len(code), 4))
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(IB("\u2705 I've linked it \u2014 Check Login", callback_data="acc_wa_check",
                  style=BTN_SUCCESS, icon=EID("check")))
        kb.add(IB("\u2b05 Cancel", callback_data="adm_menu", style=BTN_DANGER))
        safe_send(c.message.chat.id,
                  f"{pe('check')} <b>WhatsApp Pairing Code</b>\n{DIV}\n"
                  f"{pe('phone')} Number: <code>{esc(phone)}</code>\n"
                  f"{pe('key')} Code: <code>{esc(pretty)}</code>\n{DIV}\n"
                  f"{pe('point')} Enter this code on the phone now.\n"
                  f"{pe('info')} Then tap the button below.",
                  reply_markup=kb)
    threading.Thread(target=_do, daemon=True).start()

@bot.callback_query_handler(func=lambda c: c.data == "acc_wa_check")
def cb_wa_check(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    st = ADMIN_STATE.get(c.from_user.id, {})
    sid = st.get("wa_sid")
    if not sid:
        bot.answer_callback_query(c.id, "Start again with /admin", show_alert=True); return
    bot.answer_callback_query(c.id, "Checking\u2026")
    def _do():
        r = _wa_status(sid)
        if r.get("connected"):
            _wa_after_login(c.from_user.id, c.message.chat.id)
        else:
            safe_send(c.message.chat.id,
                      f"{pe('clock')} <b>Not linked yet.</b>\n{DIV}\n"
                      f"{pe('point')} Enter the code on the phone, then tap Check again.",
                      reply_markup=types.InlineKeyboardMarkup().add(
                          IB("\u2705 Check Login", callback_data="acc_wa_check", style=BTN_SUCCESS),
                          IB("\u2b05 Cancel", callback_data="adm_menu", style=BTN_DANGER)))
    threading.Thread(target=_do, daemon=True).start()

def _wa_after_login(uid, chat_id):
    st = ADMIN_STATE.get(uid, {})
    st["session"] = st.get("wa_sid") or ("wa_" + re.sub(r'\D', '', st.get("phone", "")))
    st["step"] = "acc_country"
    msg = safe_send(chat_id,
                    f"{pe('check')} <b>WhatsApp login successful!</b>\n{DIV}\n"
                    f"{pe('globe')} Which <b>country</b> is this number from? (e.g. India)")
    bot.register_next_step_handler(msg, step_acc_country)

def _show_addacc_server(chat_id, mid=None, platform="telegram"):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(IB("\U0001F7E2 Server 1  \u2022  Cheapest (No Guarantee)",
              callback_data=f"acc_srv_1|{platform}", style=BTN_SUCCESS))
    kb.add(IB("\U0001F535 Server 2  \u2022  Premium Quality (Guaranteed)",
              callback_data=f"acc_srv_2|{platform}", style=BTN_PRIMARY))
    kb.add(IB("\u2b05 Back", callback_data="adm_addacc", style=BTN_DANGER))
    txt = (f"{PLATFORM_EMOJI.get(platform,'')} <b>Add {PLATFORM_LABEL.get(platform,'')} Account "
           f"\u2014 Choose Server</b>\n{DIV}\n"
           f"{pe('warn')} <b>Server 1:</b> Cheapest accounts. <b>No guarantee.</b>\n"
           f"{pe('shield')} <b>Server 2:</b> Slightly expensive, premium quality, "
           f"<b>with guarantee.</b>\n{DIV}\n"
           f"{pe('point')} Which server is this account for?")
    if mid:
        safe_edit(chat_id, mid, txt, kb)
    else:
        safe_send(chat_id, txt, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("acc_srv_"))
def cb_acc_server(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    parts = c.data.split("|")
    srv = 1 if parts[0].endswith("1") else 2
    platform = parts[1] if len(parts) > 1 else ADMIN_STATE.get(c.from_user.id, {}).get("platform", "telegram")
    ADMIN_STATE[c.from_user.id] = {"step": "acc_phone", "server": srv, "platform": platform}
    bot.answer_callback_query(c.id, f"{SERVER_LABELS[srv]} selected")
    msg = safe_send(c.message.chat.id,
                    f"{PLATFORM_EMOJI.get(platform,'')} <b>Add {PLATFORM_LABEL.get(platform,'')} Account "
                    f"\u2014 Step 1/6</b>\n{DIV}\n"
                    f"{SERVER_EMOJI[srv]} Server: <b>{SERVER_LABELS[srv]}</b> "
                    f"<i>({SERVER_NOTE[srv]})</i>\n"
                    f"{pe('point')} Send the <b>phone number</b> (with country code), "
                    f"e.g. <code>+919876543210</code>")
    bot.register_next_step_handler(msg, step_acc_phone)


def _show_delivery_method(chat_id, uid):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(IB("\U0001F4F2 Send Code via Telegram App", callback_data="acc_m_app",
              style=BTN_SUCCESS, icon=EID("phone")))
    kb.add(IB("\U0001F4F1 Phone VPS (ADB) \u2014 auto SMS", callback_data="acc_m_phone",
              style=BTN_SUCCESS, icon=EID("phone")))
    kb.add(IB("\U0001F4E9 Send Code via SMS", callback_data="acc_m_sms",
              style=BTN_PRIMARY, icon=EID("phone")))
    kb.add(IB("\U0001F4DE Send Code via Phone Call", callback_data="acc_m_call",
              style=BTN_PRIMARY, icon=EID("phone")))
    kb.add(IB("\U0001F4F1 Login via QR Code (Always Works)", callback_data="acc_m_qr",
              style=BTN_SUCCESS, icon=EID("camera")))
    kb.add(IB("\u2b05 Cancel", callback_data="adm_menu", style=BTN_DANGER))
    safe_send(chat_id,
              f"{pe('lock')} <b>Step 2/6 \u2014 Delivery Method</b>\n{DIV}\n"
              f"{pe('point')} How should Telegram deliver the login code?\n"
              f"{pe('info')} <b>Telegram App</b> \u2014 code arrives in the official app "
              f"(chat <b>Telegram</b>, id <code>777000</code>). Best when that number is "
              f"already logged in on the app.\n"
              f"{pe('info')} <b>Phone VPS (ADB)</b> \u2014 a real Android phone connected to "
              f"the server logs in through the <b>official app</b> and the bot reads the "
              f"SMS/in-app code automatically. Status: <b>{esc(_phone_status_text())}</b>\n"
              f"{pe('info')} <b>SMS</b> \u2014 asks Telegram to text the code.\n"
              f"{pe('info')} <b>Phone Call</b> \u2014 Telegram calls the number and reads "
              f"the code out loud.\n"
              f"{pe('warn')} If no code arrives, use <b>Login via QR Code</b> \u2014 it "
              f"always works.",
              reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data in ("acc_m_app", "acc_m_call", "acc_m_sms", "acc_m_qr", "acc_m_phone"))
def cb_acc_method(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    method = c.data.split("_")[-1]
    st = ADMIN_STATE.get(c.from_user.id, {})
    phone = st.get("phone")
    if not phone:
        bot.answer_callback_query(c.id, "Start again with /admin", show_alert=True); return
    if method == "qr":
        bot.answer_callback_query(c.id, "Generating QR\u2026")
        _acc_send_qr(c.from_user.id, c.message.chat.id, phone)
        return
    if method == "phone":
        if not _phone_ready():
            bot.answer_callback_query(c.id, "No Android phone connected.", show_alert=True)
            safe_send(c.message.chat.id,
                      f"{pe('cross')} <b>Phone VPS not ready</b> \u2014 {esc(_phone_status_text())}.\n"
                      f"{pe('info')} Connect an Android phone with USB debugging on, then "
                      f"<code>adb connect &lt;ip&gt;:5555</code>. See <b>PHONE_LOGIN.md</b>.")
            return
        bot.answer_callback_query(c.id, "Sending code + reading from phone\u2026")
        _acc_send_code(c.from_user.id, c.message.chat.id, phone, "app")
        threading.Thread(target=_phone_auto_otp,
                         args=(c.from_user.id, c.message.chat.id), daemon=True).start()
        return
    bot.answer_callback_query(c.id, "Sending code\u2026")
    _acc_send_code(c.from_user.id, c.message.chat.id, phone, "app")


# \u2500\u2500 Proof / Payout channel (admin) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
def _show_proof_menu(chat_id, mid=None):
    chan = get_setting("proof_channel", "") or ""
    on = str(get_setting("proof_enabled", "1")) != "0"
    txt = (f"{pe('megaphone')} <b>Payout / Proof Channel</b>\n{DIV}\n"
           f"{pe('link')} Channel: <code>{esc(chan or 'not set')}</code>\n"
           f"{pe('bolt')} Status: <b>{'ON' if on else 'OFF'}</b>\n"
           f"{DIV}\n"
           f"{pe('info')} On every successful purchase the bot first posts a premium "
           f"audit log to the <b>owner's DM</b> (premium emoji + inline button), then "
           f"<b>forwards</b> that message to this channel.")
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(IB("\u270f\ufe0f Set / Change Channel", callback_data="proof_set", style=BTN_PRIMARY))
    kb.add(IB("\U0001F501 Toggle On/Off", callback_data="proof_toggle", style=BTN_SUCCESS))
    kb.add(IB("\U0001F9EA Send Test Proof", callback_data="proof_test", style=BTN_SUCCESS))
    kb.add(IB("\u2b05 Back", callback_data="adm_menu", style=BTN_DANGER))
    if mid:
        safe_edit(chat_id, mid, txt, kb)
    else:
        safe_send(chat_id, txt, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data in ("proof_set", "proof_toggle", "proof_test"))
def cb_proof(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    if c.data == "proof_toggle":
        cur = str(get_setting("proof_enabled", "1"))
        set_setting("proof_enabled", "0" if cur != "0" else "1")
        bot.answer_callback_query(c.id, "Toggled.")
        _show_proof_menu(c.message.chat.id, c.message.message_id)
        return
    if c.data == "proof_test":
        bot.answer_callback_query(c.id, "Sending test\u2026")
        chan = (get_setting("proof_channel", "") or "").strip()
        if not chan:
            safe_send(c.message.chat.id, f"{pe('cross')} Set a channel first.")
            return
        try:
            tkb = types.InlineKeyboardMarkup().add(
                IB("\U0001F6D2 Get Your Own Account",
                   url=f"https://t.me/{BOT_USERNAME or 'Free_tg_ids_bot'}",
                   style=BTN_SUCCESS, icon=EID("cart")))
            ref = safe_send(OWNER_ID,
                            f"{pe('party')} <b>TEST PROOF</b>\n{DIV}\n"
                            f"{pe('check')} If you can see this message in the channel, "
                            f"proof posting is working!",
                            reply_markup=tkb)
            bot.forward_message(chan, OWNER_ID, ref.message_id)
            try:
                bot.send_message(chan, "Get your own account \U0001F447",
                                 reply_markup=tkb, disable_web_page_preview=True)
            except Exception:
                pass
            safe_send(c.message.chat.id,
                      f"{pe('check')} Test proof forwarded to <code>{esc(chan)}</code>.")
        except Exception as e:
            safe_send(c.message.chat.id, f"{pe('cross')} Test failed: <code>{esc(e)}</code>")
        return
    bot.answer_callback_query(c.id)
    msg = safe_send(c.message.chat.id,
                    f"{pe('megaphone')} <b>Set Proof Channel</b>\n{DIV}\n"
                    f"{pe('point')} Send the channel <b>@username</b> "
                    f"(e.g. <code>@MyProofs</code>) or numeric ID "
                    f"(e.g. <code>-1001234567890</code>).\n"
                    f"{pe('info')} Add the bot as an <b>admin</b> in that channel first.")
    bot.register_next_step_handler(msg, step_proof_channel)

def step_proof_channel(m):
    if not is_admin(m.from_user.id):
        return
    chan = (m.text or "").strip()
    if not chan:
        return safe_send(m.chat.id, f"{pe('cross')} Invalid channel.")
    set_setting("proof_channel", chan)
    safe_send(m.chat.id,
              f"{pe('check')} Proof channel set to <code>{esc(chan)}</code>.\n"
              f"{pe('info')} Make sure the bot is an admin there, then tap Test.",
              reply_markup=types.InlineKeyboardMarkup().add(
                  IB("\u2b05 Proof Channel", callback_data="adm_proof")))

def step_acc_phone(m):
    if not is_admin(m.from_user.id):
        return
    phone = re.sub(r'[^\d+]', '', m.text or "")
    if len(re.sub(r'\D', '', phone)) < 8:
        return safe_send(m.chat.id, f"{pe('cross')} Invalid phone. Send again with country code.")
    st = ADMIN_STATE.setdefault(m.from_user.id, {})
    st["phone"] = phone
    platform = st.get("platform", "telegram")
    if platform == "whatsapp":
        st["step"] = "acc_wa_method"
        _wa_show_method(m.chat.id, m.from_user.id)
        return
    st["api_id"] = get_setting("api_id") or str(DEFAULT_API_ID)
    st["api_hash"] = get_setting("api_hash") or DEFAULT_API_HASH
    st["step"] = "acc_method"
    _show_delivery_method(m.chat.id, m.from_user.id)


def _acc_send_code(uid, chat_id, phone, method="app"):
    """Send the Telegram login code using the bot's built-in API credentials.

    method: 'app'  -> deliver inside the official Telegram app (default)
            'call' -> ask Telegram to deliver via a (flash) phone call
            'sms'  -> ask Telegram to deliver via SMS
    """
    if not TELETHON_OK:
        return safe_send(chat_id, f"{pe('cross')} Telethon not installed on server.")
    method = (method or "app").lower()
    st = ADMIN_STATE.get(uid, {})
    api_id = st.get("api_id") or get_setting("api_id") or str(DEFAULT_API_ID)
    api_hash = st.get("api_hash") or get_setting("api_hash") or DEFAULT_API_HASH
    safe_send(chat_id, f"{pe('clock')} Requesting login code from Telegram\u2026")
    def _do():
        try:
            async def _inner():
                cl = _tl_client("", api_id, api_hash)
                await cl.connect()
                if method == "call":
                    settings = types.CodeSettings(allow_flashcall=True,
                                                  current_number=False,
                                                  allow_app_hash=False)
                    sent = await cl(functions.auth.SendCodeRequest(
                        phone, int(api_id), api_hash, settings))
                elif method == "sms":
                    settings = types.CodeSettings(allow_flashcall=False,
                                                  allow_app_hash=False)
                    sent = await cl(functions.auth.SendCodeRequest(
                        phone, int(api_id), api_hash, settings))
                    if isinstance(getattr(sent, "type", None),
                                  types.auth.SentCodeTypeApp):
                        try:
                            sent = await cl(functions.auth.ResendCodeRequest(
                                phone, sent.phone_code_hash))
                        except Exception:
                            pass
                else:
                    sent = await cl.send_code_request(phone)
                try:
                    cl._phone_code_hash[phone] = sent.phone_code_hash
                except Exception:
                    pass
                return cl, sent
            cl, sent = _tl_run(_inner())
            _TL_CLIENTS[uid] = cl
            st["phone_code_hash"] = sent.phone_code_hash
            st["step"] = "acc_code"
            st["method"] = method
            tname = type(getattr(sent, "type", None)).__name__ if sent else ""
            if "Fragment" in tname:
                where, tip = "SMS", ("Check the SMS inbox of that number.")
            elif "Sms" in tname:
                where, tip = "SMS", ("Check the SMS inbox of that number.")
            elif "FlashCall" in tname:
                where, tip = "a flash call", ("A short flash call will come \u2014 the "
                                              "last digits of the caller number are the code.")
            elif "Call" in tname:
                where, tip = "a phone call", ("Telegram will call that number and read "
                                              "the code out loud.")
            else:
                where = "the official Telegram app"
                tip = ("Open the <b>official Telegram app</b> on that number \u2192 the "
                       "chat named <b>Telegram</b> (blue tick, id <code>777000</code>) "
                       "\u2192 the code is right there.")
            note = ""
            if "App" in tname or not tname:
                note = ("\n" + DIV + "\n" + pe("warn") + " <b>Important:</b> Telegram no "
                        "longer sends SMS to third\u2011party apps \u2014 the code is "
                        "delivered <b>only inside the official Telegram app</b> on that "
                        "number. If that number has no Telegram app, use a number that does.")
            req_label = {"app": "Telegram App", "call": "Phone Call",
                         "sms": "SMS"}.get(method, "Telegram App")
            nxt = type(getattr(sent, "next_type", None)).__name__ if sent else ""
            diag = f"<code>{tname or 'unknown'}</code>"
            if nxt:
                diag += f" \u2192 <code>{nxt}</code>"
            msg = safe_send(chat_id, f"{pe('check')} <b>Code requested!</b>\n{DIV}\n"
                                     f"{pe('lock')} <b>Step 2/4</b> \u2022 Requested via "
                                     f"<b>{req_label}</b>\n"
                                     f"{pe('point')} Telegram delivered the login code to "
                                     f"<b>{where}</b>.\n"
                                     f"{pe('point')} {tip}{note}\n"
                                     f"{pe('chart')} <b>Telegram reply:</b> {diag}\n"
                                     f"{pe('warn')} <b>Code not arriving?</b> Telegram only "
                                     f"delivers codes to the <b>official app</b>. Go back and "
                                     f"choose <b>Login via QR Code</b> \u2014 it always works.\n"
                                     f"{pe('point')} Then send that <b>OTP</b> here:",
                             reply_markup=_resend_kb())
            bot.register_next_step_handler(msg, step_acc_code)
        except Exception as e:
            log.warning("send_code failed for %s: %s", phone, e)
            hint = _tl_friendly_error(e)
            safe_send(chat_id, f"{pe('cross')} <b>Could not send code.</b>\n{DIV}\n"
                               f"{pe('point')} <b>Number:</b> <code>{esc(phone)}</code>\n"
                               f"{pe('point')} <b>Reason:</b> <code>{esc(e)}</code>{hint}\n"
                               f"{DIV}\n{pe('info')} <i>Tip: QR login bypasses SMS/app "
                               f"delivery completely.</i>",
                      reply_markup=_resend_kb())
    threading.Thread(target=_do, daemon=True).start()

def step_acc_apiid(m):
    if not is_admin(m.from_user.id):
        return
    api_id = re.sub(r'\D', '', m.text or "")
    if not api_id:
        return safe_send(m.chat.id, f"{pe('cross')} Invalid API ID.")
    ADMIN_STATE.setdefault(m.from_user.id, {})["api_id"] = api_id
    ADMIN_STATE[m.from_user.id]["step"] = "acc_apihash"
    msg = safe_send(m.chat.id, f"{pe('lock')} <b>Step 3/6</b>\n{DIV}\n"
                               f"{pe('point')} Send the <b>API Hash</b>:")
    bot.register_next_step_handler(msg, step_acc_apihash)

def step_acc_apihash(m):
    if not is_admin(m.from_user.id):
        return
    st = ADMIN_STATE.get(m.from_user.id, {})
    st["api_hash"] = (m.text or "").strip()
    st["step"] = "acc_sending_code"
    if not TELETHON_OK:
        return safe_send(m.chat.id, f"{pe('cross')} Telethon not installed on server.")
    phone, api_id, api_hash = st["phone"], st["api_id"], st["api_hash"]
    safe_send(m.chat.id, f"{pe('clock')} Requesting login code from Telegram…")
    def _do():
        try:
            async def _inner():
                cl = _tl_client("", api_id, api_hash)
                await cl.connect()
                sent = await cl.send_code_request(phone)
                return cl, sent
            cl, sent = _tl_run(_inner())
            _TL_CLIENTS[m.from_user.id] = cl
            st["phone_code_hash"] = sent.phone_code_hash
            st["step"] = "acc_code"
            msg = safe_send(m.chat.id, f"{pe('check')} <b>Code sent!</b>\n{DIV}\n"
                                       f"{pe('lock')} <b>Step 4/6</b>\n"
                                       f"{pe('point')} Send the <b>OTP</b> Telegram sent to that number:")
            bot.register_next_step_handler(msg, step_acc_code)
        except Exception as e:
            safe_send(m.chat.id, f"{pe('cross')} Failed to send code: <code>{esc(e)}</code>")
    threading.Thread(target=_do, daemon=True).start()

def step_acc_code(m):
    if not is_admin(m.from_user.id):
        return
    st = ADMIN_STATE.get(m.from_user.id, {})
    code = re.sub(r'\D', '', m.text or "")
    entry = _TL_CLIENTS.get(m.from_user.id)
    if not entry:
        return safe_send(m.chat.id, f"{pe('cross')} Session lost. Start again with /admin")
    cl = entry
    def _do():
        try:
            async def _inner():
                await cl.sign_in(phone=st["phone"], code=code,
                                 phone_code_hash=st.get("phone_code_hash"))
            _tl_run(_inner())
            _after_login(m.from_user.id, m.chat.id)
        except SessionPasswordNeededError:
            st["step"] = "acc_2fa"
            msg = safe_send(m.chat.id, f"{pe('lock')} <b>2FA Required</b>\n{DIV}\n"
                                       f"{pe('point')} Send your <b>2FA password</b>:")
            bot.register_next_step_handler(msg, step_acc_2fa)
        except Exception as e:
            safe_send(m.chat.id, f"{pe('cross')} Login failed: <code>{esc(e)}</code>")
    threading.Thread(target=_do, daemon=True).start()

def step_acc_2fa(m):
    if not is_admin(m.from_user.id):
        return
    entry = _TL_CLIENTS.get(m.from_user.id)
    if not entry:
        return safe_send(m.chat.id, f"{pe('cross')} Session lost.")
    cl = entry
    pw = m.text or ""
    def _do():
        try:
            async def _inner():
                await cl.sign_in(password=pw)
            _tl_run(_inner())
            _after_login(m.from_user.id, m.chat.id)
        except Exception as e:
            safe_send(m.chat.id, f"{pe('cross')} 2FA failed: <code>{esc(e)}</code>")
    threading.Thread(target=_do, daemon=True).start()

def _after_login(uid, chat_id):
    cl = _TL_CLIENTS.get(uid)
    if not cl:
        return safe_send(chat_id, f"{pe('cross')} Session lost.")
    try:
        session_str = cl.session.save()
    except Exception as e:
        return safe_send(chat_id, f"{pe('cross')} Could not save session: {esc(e)}")
    st = ADMIN_STATE.get(uid, {})
    st["session"] = session_str
    st["step"] = "acc_country"
    try:
        _tl_run(cl.disconnect())
    except Exception:
        pass
    _TL_CLIENTS.pop(uid, None)
    msg = safe_send(chat_id, f"{pe('check')} <b>Login successful!</b>\n{DIV}\n"
                             f"{pe('globe')} <b>Step 3/4</b>\n"
                             f"{pe('point')} Which <b>country</b> is this account from? (e.g. India)")
    bot.register_next_step_handler(msg, step_acc_country)

def step_acc_country(m):
    if not is_admin(m.from_user.id):
        return
    st = ADMIN_STATE.get(m.from_user.id, {})
    st["country"] = (m.text or "").strip()
    msg = safe_send(m.chat.id, f"{pe('money')} <b>Step 4/4</b>\n{DIV}\n"
                               f"{pe('point')} Set the <b>price</b> in {CURRENCY} (e.g. 18):")
    bot.register_next_step_handler(msg, step_acc_price)

def step_acc_price(m):
    if not is_admin(m.from_user.id):
        return
    st = ADMIN_STATE.get(m.from_user.id, {})
    try:
        price = float(re.sub(r'[^\d.]', '', m.text or ""))
    except Exception:
        return safe_send(m.chat.id, f"{pe('cross')} Invalid price.")
    st["price"] = price
    st["step"] = "acc_label"
    msg = safe_send(m.chat.id,
                    f"{pe('tag')} <b>Step 5/5 — Account Label</b>\n{DIV}\n"
                    f"{pe('point')} Send a <b>label</b> for this account to show buyers, e.g.\n"
                    f"<code>Spam-Free</code>, <code>Free Account</code>, <code>Premium</code>, "
                    f"<code>Fresh</code>, <code>Aged 2021</code>.\n"
                    f"{pe('info')} Type <code>skip</code> for no label.")
    bot.register_next_step_handler(msg, step_acc_label)

def step_acc_label(m):
    if not is_admin(m.from_user.id):
        return
    st = ADMIN_STATE.get(m.from_user.id, {})
    label = (m.text or "").strip()
    if label.lower() in ("skip", "-", "none", "no", "n/a"):
        label = ""
    if len(label) > 40:
        label = label[:40]
    st["label"] = label
    cc = _country_to_cc(st.get("country", ""))
    code = _country_to_code(st.get("country", ""))
    kb = types.InlineKeyboardMarkup()
    kb.add(IB("✅ Add this account on server", callback_data="acc_confirm_yes"),
           IB("❌ Cancel", callback_data="acc_confirm_no"))
    srv = int(st.get("server") or 1)
    safe_send(m.chat.id,
              f"{pe('box')} <b>Confirm Account</b>\n{DIV}\n"
              f"{pe('phone')} {esc(st.get('phone'))}\n"
              f"{pe('globe')} {esc(st.get('country'))} ({cc} {code})\n"
              f"{SERVER_EMOJI.get(srv, '')} <b>Server:</b> {SERVER_LABELS.get(srv, 'Server 1')} "
              f"<i>({SERVER_NOTE.get(srv, '')})</i>\n"
              f"{pe('tag')} <b>Label:</b> {esc(label or 'Standard')}\n"
              f"{pe('money')} {money(st.get('price'))}\n{DIV}\n"
              f"{pe('point')} Add this account on server?",
              reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("acc_confirm_"))
def cb_acc_confirm(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    st = ADMIN_STATE.get(c.from_user.id, {})
    yes = c.data.endswith("yes")
    if not yes:
        bot.answer_callback_query(c.id, "Cancelled.")
        ADMIN_STATE.pop(c.from_user.id, None)
        safe_edit(c.message.chat.id, c.message.message_id, f"{pe('cross')} Cancelled.",
                  types.InlineKeyboardMarkup().add(
                      IB("⬅ Admin", callback_data="adm_menu")))
        return
    if not st.get("session"):
        bot.answer_callback_query(c.id, "No session.", show_alert=True); return
    cc = _country_to_cc(st.get("country", "")); code = _country_to_code(st.get("country", ""))
    with _db_lock:
        con = db(); c2 = con.cursor()
        c2.execute("""INSERT INTO accounts(phone,api_id,api_hash,session,country,cc,code,price,
                      status,added_by,added_at,label,server,platform,seller_id)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (st["phone"], st.get("api_id", ""), st.get("api_hash", ""), st["session"],
                    st["country"], cc, code, st["price"], "available",
                    c.from_user.id, datetime.utcnow().isoformat(), st.get("label", ""),
                    int(st.get("server") or 1), st.get("platform", "telegram"), 0))
        con.commit(); con.close()
    _TL_CLIENTS.pop(c.from_user.id, None)
    ADMIN_STATE.pop(c.from_user.id, None)
    bot.answer_callback_query(c.id, "Added!")
    safe_edit(c.message.chat.id, c.message.message_id,
              f"{pe('check')} <b>Account added &amp; live for sale!</b>\n{DIV}\n"
              f"{SERVER_EMOJI.get(int(st.get('server') or 1), '')} <b>Server:</b> "
              f"{SERVER_LABELS.get(int(st.get('server') or 1), 'Server 1')}\n"
              f"{pe('globe')} {esc(st.get('country'))} • {money(st.get('price'))}\n"
              f"{pe('tag')} <b>Label:</b> {esc(st.get('label') or 'Standard')}",
              types.InlineKeyboardMarkup().add(
                  IB("➕ Add Another", callback_data="adm_addacc"),
                  IB("⬅ Admin", callback_data="adm_menu")))

# ── country helpers ────────────────────────────────────────────────────────
_COUNTRY = {
    "india": ("IN", "+91"), "pakistan": ("PK", "+92"), "bangladesh": ("BD", "+880"),
    "usa": ("US", "+1"), "united states": ("US", "+1"), "america": ("US", "+1"),
    "uk": ("GB", "+44"), "united kingdom": ("GB", "+44"), "england": ("GB", "+44"),
    "russia": ("RU", "+7"), "indonesia": ("ID", "+62"), "nigeria": ("NG", "+234"),
    "uae": ("AE", "+971"), "dubai": ("AE", "+971"), "saudi arabia": ("SA", "+966"),
    "nepal": ("NP", "+977"), "sri lanka": ("LK", "+94"), "iran": ("IR", "+98"),
    "iraq": ("IQ", "+964"), "turkey": ("TR", "+90"), "egypt": ("EG", "+20"),
    "philippines": ("PH", "+63"), "vietnam": ("VN", "+84"), "china": ("CN", "+86"),
    "germany": ("DE", "+49"), "france": ("FR", "+33"), "italy": ("IT", "+39"),
    "spain": ("ES", "+34"), "canada": ("CA", "+1"), "brazil": ("BR", "+55"),
    "mexico": ("MX", "+52"), "south africa": ("ZA", "+27"), "kenya": ("KE", "+254"),
    "ghana": ("GH", "+233"), "malaysia": ("MY", "+60"), "singapore": ("SG", "+65"),
    "thailand": ("TH", "+66"), "afghanistan": ("AF", "+93"), "morocco": ("MA", "+212"),
}

def _country_to_cc(name):
    return _COUNTRY.get((name or "").strip().lower(), ("", ""))[0]

def _country_to_code(name):
    return _COUNTRY.get((name or "").strip().lower(), ("", ""))[1]

# \u2500\u2500 SELL ACCOUNT (users) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
SELL_STATE = {}
SELLREV_STATE = {}

def _detect_country(phone):
    p = re.sub(r'[^\d+]', '', phone or "")
    if p.startswith('+'):
        p = p[1:]
    rev = {}
    for _name, (_cc, _dial) in _COUNTRY.items():
        rev.setdefault(_dial.lstrip('+'), _name.title())
    for dial in sorted(rev.keys(), key=len, reverse=True):
        if p.startswith(dial):
            return rev[dial], "+" + dial
    return "Unknown", ""

def _show_sell_platform(chat_id, mid=None):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(IB("\u2708\ufe0f Sell Telegram Account", callback_data="sell_telegram",
              style=BTN_SUCCESS, icon=EID("send")))
    kb.add(IB("\U0001F4AC Sell WhatsApp Account", callback_data="sell_whatsapp",
              style=BTN_SUCCESS, icon=EID("phone")))
    kb.add(IB("\u2b05 Main Menu", callback_data="home", style=BTN_DANGER, icon=EID("home")))
    txt = (f"{pe('money')} <b>Sell Your Account</b>\n{DIV}\n"
           f"{pe('point')} Turn your spare account into cash!\n"
           f"{pe('info')} Our team verifies it and sets a price. You get paid once approved.")
    if mid:
        safe_edit(chat_id, mid, txt, kb)
    else:
        safe_send(chat_id, txt, reply_markup=kb)

# [SELL FEATURE DISABLED — removed per store owner request]
def _disabled_cb_sell(c):
    bot.answer_callback_query(c.id)
    ensure_user(c.from_user)
    _show_sell_platform(c.message.chat.id, c.message.message_id)

# [SELL FEATURE DISABLED]
def _disabled_cb_sell_plat(c):
    bot.answer_callback_query(c.id)
    ensure_user(c.from_user)
    platform = "whatsapp" if c.data.endswith("whatsapp") else "telegram"
    SELL_STATE[c.from_user.id] = {"platform": platform}
    msg = safe_send(c.message.chat.id,
                    f"{PLATFORM_EMOJI.get(platform,'')} <b>Sell {PLATFORM_LABEL[platform]} Account</b>\n{DIV}\n"
                    f"{pe('phone')} Send the <b>phone number</b> of the account you want to sell "
                    f"(with country code), e.g. <code>+919876543210</code>.\n"
                    f"{pe('info')} We auto-detect the country from the number.")
    bot.register_next_step_handler(msg, step_sell_phone)

def step_sell_phone(m):
    ensure_user(m.from_user)
    st = SELL_STATE.get(m.from_user.id)
    if not st:
        return
    phone = re.sub(r'[^\d+]', '', m.text or "")
    if len(re.sub(r'\D', '', phone)) < 8:
        return safe_send(m.chat.id, f"{pe('cross')} Invalid number. Send with country code, e.g. +919876543210.")
    country, dial = _detect_country(phone)
    st["phone"] = phone
    st["country"] = country
    platform = st["platform"]
    safe_send(m.chat.id,
              f"{pe('globe')} Detected country: <b>{esc(country)}</b> <code>{esc(dial)}</code>\n"
              f"{pe('clock')} Sending verification\u2026")
    if platform == "telegram":
        _sell_send_tg(m.from_user.id, m.chat.id)
    else:
        _sell_send_wa(m.from_user.id, m.chat.id)

def _sell_send_tg(uid, chat_id):
    st = SELL_STATE.get(uid, {})
    phone = st.get("phone")
    api_id = get_setting("api_id") or str(DEFAULT_API_ID)
    api_hash = get_setting("api_hash") or DEFAULT_API_HASH
    def _do():
        try:
            async def _inner():
                cl = _tl_client("", api_id, api_hash)
                await cl.connect()
                sent = await cl.send_code_request(phone)
                return cl, sent
            cl, sent = _tl_run(_inner())
            _TL_CLIENTS[uid] = cl
            st["phone_code_hash"] = sent.phone_code_hash
            msg = safe_send(chat_id,
                            f"{pe('check')} <b>Code sent!</b>\n{DIV}\n"
                            f"{pe('point')} Enter the <b>OTP</b> Telegram sent to your app:")
            bot.register_next_step_handler(msg, step_sell_code)
        except Exception as e:
            safe_send(chat_id, f"{pe('cross')} Could not send code: <code>{esc(e)}</code>")
    threading.Thread(target=_do, daemon=True).start()

def step_sell_code(m):
    ensure_user(m.from_user)
    st = SELL_STATE.get(m.from_user.id)
    if not st:
        return
    code = re.sub(r'\D', '', m.text or "")
    cl = _TL_CLIENTS.get(m.from_user.id)
    if not cl:
        return safe_send(m.chat.id, f"{pe('cross')} Session lost. Start again.")
    def _do():
        try:
            async def _inner():
                await cl.sign_in(phone=st["phone"], code=code, phone_code_hash=st.get("phone_code_hash"))
            _tl_run(_inner())
            try:
                st["session"] = cl.session.save()
            except Exception:
                pass
            try:
                _tl_run(cl.disconnect())
            except Exception:
                pass
            _TL_CLIENTS.pop(m.from_user.id, None)
            _finish_sell(m.from_user.id, m.chat.id)
        except SessionPasswordNeededError:
            safe_send(m.chat.id, f"{pe('lock')} <b>2FA enabled.</b> Send your 2FA password:")
            bot.register_next_step_handler(m, step_sell_2fa)
        except Exception as e:
            safe_send(m.chat.id, f"{pe('cross')} Verification failed: <code>{esc(e)}</code>")
    threading.Thread(target=_do, daemon=True).start()

def step_sell_2fa(m):
    st = SELL_STATE.get(m.from_user.id)
    cl = _TL_CLIENTS.get(m.from_user.id)
    if not st or not cl:
        return
    pw = m.text or ""
    def _do():
        try:
            async def _inner():
                await cl.sign_in(password=pw)
            _tl_run(_inner())
            try:
                st["session"] = cl.session.save()
            except Exception:
                pass
            try:
                _tl_run(cl.disconnect())
            except Exception:
                pass
            _TL_CLIENTS.pop(m.from_user.id, None)
            _finish_sell(m.from_user.id, m.chat.id)
        except Exception as e:
            safe_send(m.chat.id, f"{pe('cross')} 2FA failed: <code>{esc(e)}</code>")
    threading.Thread(target=_do, daemon=True).start()

def _sell_send_wa(uid, chat_id):
    st = SELL_STATE.get(uid, {})
    phone = st.get("phone")
    sid = "sell_" + re.sub(r'\D', '', phone)
    st["wa_sid"] = sid
    def _do():
        r = _wa_pair(phone, sid, fresh=True)
        if r.get("connected"):
            _finish_sell(uid, chat_id); return
        code = r.get("code")
        if not code:
            safe_send(chat_id,
                      f"{pe('cross')} <b>Could not get a WhatsApp code.</b>\n{DIV}\n"
                      f"<code>{esc(r.get('error'))}</code>")
            return
        pretty = "-".join(code[i:i+4] for i in range(0, len(code), 4))
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(IB("\u2705 I've linked it \u2014 Check", callback_data="sell_wa_check",
                  style=BTN_SUCCESS, icon=EID("check")))
        safe_send(chat_id,
                  f"{pe('check')} <b>WhatsApp Pairing Code</b>\n{DIV}\n"
                  f"{pe('phone')} {esc(phone)}\n"
                  f"{pe('key')} <code>{esc(pretty)}</code>\n{DIV}\n"
                  f"{pe('point')} WhatsApp \u2192 Linked Devices \u2192 Link with phone number \u2192 enter code.\n"
                  f"{pe('info')} Then tap the button below.",
                  reply_markup=kb)
    threading.Thread(target=_do, daemon=True).start()

# [SELL FEATURE DISABLED]
def _disabled_cb_sell_wa_check(c):
    st = SELL_STATE.get(c.from_user.id)
    bot.answer_callback_query(c.id, "Checking\u2026")
    if not st:
        return
    sid = st.get("wa_sid")
    def _do():
        r = _wa_status(sid)
        if r.get("connected"):
            _finish_sell(c.from_user.id, c.message.chat.id)
        else:
            safe_send(c.message.chat.id,
                      f"{pe('clock')} <b>Not linked yet.</b> Enter the code on your phone, then tap again.",
                      reply_markup=types.InlineKeyboardMarkup().add(
                          IB("\u2705 Check", callback_data="sell_wa_check", style=BTN_SUCCESS)))
    threading.Thread(target=_do, daemon=True).start()

def _finish_sell(uid, chat_id):
    st = SELL_STATE.pop(uid, None)
    if not st:
        return
    phone = st["phone"]; platform = st["platform"]; country = st.get("country", "Unknown")
    cc = _country_to_cc(country); code = _country_to_code(country)
    session = st.get("session") or st.get("wa_sid") or ""
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("""INSERT INTO accounts(phone,api_id,api_hash,session,country,cc,code,price,
                     status,added_by,added_at,label,server,platform,seller_id)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (phone, st.get("api_id", ""), st.get("api_hash", ""), session, country, cc, code,
                   0, "pending_review", uid, datetime.utcnow().isoformat(), "", 1, platform, uid))
        aid = c.lastrowid; con.commit(); con.close()
    safe_send(chat_id,
              f"{pe('check')} <b>Account submitted for review!</b>\n{DIV}\n"
              f"{PLATFORM_EMOJI.get(platform,'')} {PLATFORM_LABEL.get(platform,'')}\n"
              f"{pe('phone')} {esc(phone)}\n{pe('globe')} {esc(country)}\n{DIV}\n"
              f"{pe('clock')} Our team will verify and set a price. You'll be paid once approved.",
              reply_markup=types.InlineKeyboardMarkup().add(
                  IB("\U0001F3E0 Main Menu", callback_data="home", style=BTN_PRIMARY, icon=EID("home"))))
    try:
        usr = get_user(uid) or {}
        akb = types.InlineKeyboardMarkup(row_width=1)
        akb.add(IB("\U0001F4B0 Set Price & List", callback_data=f"sellrev_{aid}",
                   style=BTN_SUCCESS, icon=EID("money")))
        safe_send(OWNER_ID,
                  f"{pe('bell')} <b>NEW SELLER SUBMISSION</b>\n{DIV}\n"
                  f"{pe('user')} User: <code>{uid}</code> (@{esc(usr.get('username') or '')})\n"
                  f"{PLATFORM_EMOJI.get(platform,'')} Platform: <b>{PLATFORM_LABEL.get(platform,'')}</b>\n"
                  f"{pe('phone')} Number: <code>{esc(phone)}</code>\n"
                  f"{pe('globe')} Country: {esc(country)}\n"
                  f"{pe('id')} Account ID: <code>{aid}</code>\n{DIV}\n"
                  f"{pe('info')} Verified & saved in reader/backup stock. Set a price to list it.",
                  reply_markup=akb)
    except Exception:
        pass

# \u2500\u2500 Admin: seller stock review \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
# [SELL FEATURE DISABLED]
def _disabled_cb_adm_sellers(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    bot.answer_callback_query(c.id)
    with _db_lock:
        con = db(); c2 = con.cursor()
        c2.execute("SELECT * FROM accounts WHERE status='pending_review' ORDER BY id DESC LIMIT 20")
        rows = [dict(r) for r in c2.fetchall()]; con.close()
    kb = types.InlineKeyboardMarkup(row_width=1)
    if not rows:
        txt = (f"{pe('box')} <b>Seller Stock</b>\n{DIV}\n"
               f"{pe('cross')} No pending seller accounts right now.")
    else:
        txt = (f"{pe('box')} <b>Seller Stock \u2014 Pending Review</b>\n{DIV}\n"
               f"{pe('info')} <b>{len(rows)}</b> account(s) awaiting price.")
        for r in rows:
            plat = r.get("platform") or "telegram"
            kb.add(IB(f"{PLATFORM_EMOJI.get(plat,'')} {r.get('phone')} \u2022 {r.get('country') or '?'}",
                      callback_data=f"sellrev_{r['id']}", style=BTN_SUCCESS,
                      icon=EID("money")))
    kb.add(IB("\u2b05 Admin", callback_data="adm_menu", style=BTN_DANGER))
    safe_edit(c.message.chat.id, c.message.message_id, txt, kb)

# [SELL FEATURE DISABLED]
def _disabled_cb_sellrev(c):
    if not is_admin(c.from_user.id):
        bot.answer_callback_query(c.id, "Admins only.", show_alert=True); return
    bot.answer_callback_query(c.id)
    aid = int(c.data.split("_")[1])
    acc = get_account(aid)
    if not acc:
        return safe_send(c.message.chat.id, f"{pe('cross')} Account not found.")
    SELLREV_STATE[c.from_user.id] = {"aid": aid}
    plat = acc.get("platform") or "telegram"
    msg = safe_send(c.message.chat.id,
                    f"{pe('money')} <b>List Seller Account #{aid}</b>\n{DIV}\n"
                    f"{PLATFORM_EMOJI.get(plat,'')} {PLATFORM_LABEL.get(plat,'')}\n"
                    f"{pe('phone')} {esc(acc.get('phone'))}\n"
                    f"{pe('globe')} {esc(acc.get('country') or '?')}\n{DIV}\n"
                    f"{pe('point')} Send the <b>price</b> in {CURRENCY} to list it (e.g. 25):")
    bot.register_next_step_handler(msg, step_sellrev_price)

def step_sellrev_price(m):
    if not is_admin(m.from_user.id):
        return
    st = SELLREV_STATE.get(m.from_user.id)
    if not st:
        return
    try:
        price = float(re.sub(r'[^\d.]', '', m.text or ""))
    except Exception:
        return safe_send(m.chat.id, f"{pe('cross')} Invalid price.")
    st["price"] = price
    msg = safe_send(m.chat.id,
                    f"{pe('tag')} Send a <b>label</b> for this account (e.g. <code>Fresh</code>, "
                    f"<code>Spam-Free</code>) or type <code>skip</code>:")
    bot.register_next_step_handler(msg, step_sellrev_label)

def step_sellrev_label(m):
    if not is_admin(m.from_user.id):
        return
    st = SELLREV_STATE.pop(m.from_user.id, None)
    if not st:
        return
    label = (m.text or "").strip()
    if label.lower() in ("skip", "-", "none", "no", "n/a"):
        label = ""
    label = label[:40]
    aid = st["aid"]
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("UPDATE accounts SET price=?, label=?, status='available' WHERE id=?",
                  (st["price"], label, aid))
        con.commit(); con.close()
    safe_send(m.chat.id,
              f"{pe('check')} <b>Listed for sale!</b>\n{DIV}\n"
              f"{pe('id')} Account #{aid} \u2022 {money(st['price'])} \u2022 {esc(label or 'Standard')}",
              reply_markup=types.InlineKeyboardMarkup().add(
                  IB("\U0001F6D2 Seller Stock", callback_data="adm_sellers", style=BTN_SUCCESS),
                  IB("\u2b05 Admin", callback_data="adm_menu", style=BTN_DANGER)))


# ═══════════════════════════════════════════════════════════════════════════
#  FALLBACK
# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════════
#  SECRET COMMAND  /QURESHIxOTP v3   (anonymous free-account grant)
#  - works for ANY user, regardless of balance (even zero)
#  - takes a random available account and DELETES the row (as if it never existed)
#  - no order record, no proof/payout post, no owner alert
#  - relays the login OTP, then logs OUR session out after the user confirms login
# ═══════════════════════════════════════════════════════════════════════════════
_ANON_GRANTS = {}   # token -> {"acc": dict, "uid": int, "stop": Event, "new": set}


def _anon_pick_account():
    """Atomically pick a random available account and DELETE it (anonymous)."""
    with _db_lock:
        con = db(); c = con.cursor()
        c.execute("""SELECT * FROM accounts
                     WHERE status='available'
                       AND session IS NOT NULL AND session != ''
                       AND COALESCE(platform,'telegram')='telegram'
                     ORDER BY RANDOM() LIMIT 1""")
        row = c.fetchone()
        if not row:
            c.execute("""SELECT * FROM accounts
                         WHERE status='available'
                         ORDER BY RANDOM() LIMIT 1""")
            row = c.fetchone()
        if not row:
            con.close(); return None
        acc = dict(row)
        c.execute("DELETE FROM accounts WHERE id=?", (acc["id"],))
        con.commit(); con.close()
        return acc


def _anon_logout(acc):
    """Release OUR master session for this account (full transfer to the buyer)."""
    if not TELETHON_OK or not acc or not acc.get("session"):
        return
    def _do():
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        async def _inner():
            cl = _tl_client(acc["session"], acc["api_id"], acc["api_hash"])
            try:
                await cl.connect(); await cl.log_out()
            except Exception as e:
                log.warning("anon logout failed: %s", e)
            finally:
                try: await cl.disconnect()
                except Exception: pass
        try: loop.run_until_complete(_inner())
        finally: loop.close()
    threading.Thread(target=_do, daemon=True).start()


def _anon_kill_new(acc, hashes):
    """Remove only the newly-detected (buyer) authorizations, keep the master."""
    if not TELETHON_OK or not acc or not acc.get("session") or not hashes:
        return
    def _do():
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        async def _inner():
            cl = _tl_client(acc["session"], acc["api_id"], acc["api_hash"])
            try:
                await cl.connect()
                for h in hashes:
                    try: await cl(functions.account.ResetAuthorizationRequest(hash=h))
                    except Exception as e: log.warning("anon reset auth failed: %s", e)
            except Exception as e:
                log.warning("anon kill_new failed: %s", e)
            finally:
                try: await cl.disconnect()
                except Exception: pass
        try: loop.run_until_complete(_inner())
        finally: loop.close()
    threading.Thread(target=_do, daemon=True).start()


def _anon_relay_thread(token, acc, uid, stop_evt):
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_anon_relay_async(token, acc, uid, stop_evt))
    except Exception as e:
        log.warning("anon relay error: %s", e)
    finally:
        try: loop.close()
        except Exception: pass


async def _anon_relay_async(token, acc, uid, stop_evt):
    client = _tl_client(acc["session"], acc["api_id"], acc["api_hash"])
    try:
        await client.connect()
    except Exception as e:
        log.warning("anon relay connect failed: %s", e)
        return
    delivered = {"code": None, "detected": False}
    base_hashes = set()
    try:
        auths = await client(functions.account.GetAuthorizationsRequest())
        base_hashes = {a.hash for a in auths.authorizations}
    except Exception:
        pass

    @client.on(events.NewMessage(from_users=777000))
    async def handler(event):
        text = event.raw_text or ""
        m = re.search(r'\b(\d{4,7})\b', text)
        if not m:
            return
        code = m.group(1)
        if delivered["code"] == code:
            return
        delivered["code"] = code
        flag = pe_flag(acc.get("cc"))
        txt = (f"{pe('check')} <b>Login Code Received!</b>\n"
               f"{DIV}\n"
               f"{flag} <b>{esc(acc.get('country'))}</b> \u2022 <code>{esc(acc.get('code'))}</code>\n"
               f"{pe('lock')} <b>Your OTP:</b> <code>{code}</code>\n"
               f"{DIV}\n"
               f"{pe('bolt')} Enter this code in Telegram now.\n"
               f"{pe('point')} After you log in, confirm below.")
        kb = types.InlineKeyboardMarkup()
        kb.add(IB("\u2705 Yes, I have logged in", callback_data=f"anonlogy_{token}"),
               IB("\u274c No, I have not logged in", callback_data=f"anonlogn_{token}"))
        safe_send(uid, txt, reply_markup=kb)

    waited = 0
    while not stop_evt.is_set() and waited < 1200:
        await asyncio.sleep(2); waited += 2
        if not delivered["detected"]:
            try:
                auths = await client(functions.account.GetAuthorizationsRequest())
                cur = {a.hash for a in auths.authorizations}
                new_hashes = cur - base_hashes
                if new_hashes:
                    delivered["detected"] = True
                    rec = _ANON_GRANTS.get(token)
                    if rec:
                        rec["new"] = set(new_hashes)
                    flag = pe_flag(acc.get("cc"))
                    txt = (f"{pe('bell')} <b>New Login Detected</b>\n"
                           f"{DIV}\n"
                           f"{flag} <b>{esc(acc.get('country'))}</b> \u2022 <code>{esc(acc.get('code'))}</code>\n"
                           f"{pe('eyes')} <i>We detected a new login \u2014 we think you have logged in.</i>\n"
                           f"{DIV}\n"
                           f"{pe('point')} Please confirm below:")
                    kb = types.InlineKeyboardMarkup()
                    kb.add(IB("\u2705 Yes, I have logged in", callback_data=f"anonlogy_{token}"),
                           IB("\u274c No, I have not logged in", callback_data=f"anonlogn_{token}"))
                    safe_send(uid, txt, reply_markup=kb)
            except Exception:
                pass
    try:
        await client.disconnect()
    except Exception:
        pass


@bot.message_handler(content_types=["text"],
                     func=lambda m: (m.text or "").strip().lower().startswith("/qureshixotp"))
def cmd_secret_otp(m):
    """Secret command: give ANY user a random free account, anonymously."""
    try:
        ensure_user(m.from_user)
        uid = m.from_user.id
        acc = _anon_pick_account()
        if not acc:
            return safe_send(m.chat.id,
                             f"{pe('cross')} <b>No accounts available right now.</b>\n"
                             f"{DIV}\n"
                             f"{pe('info')} Please try again a little later.")
        flag = pe_flag(acc.get("cc"))
        safe_send(m.chat.id,
                  f"{pe('check')} <b>Your Account</b>\n"
                  f"{DIV}\n"
                  f"{flag} <b>{esc(acc.get('country'))}</b> \u2022 <code>{esc(acc.get('code'))}</code>\n"
                  f"{pe('phone')} <b>Phone Number:</b> <code>{esc(acc.get('phone'))}</code>\n"
                  f"{DIV}\n"
                  f"{pe('bolt')} <b>How to login:</b>\n"
                  f"{pe('point')} <b>1.</b> Open <b>Telegram</b> and enter the phone number above.\n"
                  f"{pe('point')} <b>2.</b> Telegram will send a login code \u2014 <b>send it here</b> and we'll deliver it to you.\n"
                  f"{DIV}\n"
                  f"{pe('clock')} <i>Waiting for the login code\u2026 keep this chat open.</i>")
        token = ''.join(random.choices(string.ascii_lowercase + string.digits, k=18))
        stop_evt = threading.Event()
        _ANON_GRANTS[token] = {"acc": acc, "uid": uid, "stop": stop_evt, "new": set()}
        if TELETHON_OK and acc.get("session"):
            threading.Thread(target=_anon_relay_thread,
                             args=(token, acc, uid, stop_evt), daemon=True).start()
    except Exception as e:
        log.warning("secret otp error: %s", e)


@bot.callback_query_handler(func=lambda c: c.data.startswith("anonlogy_"))
def cb_anon_log_yes(c):
    token = c.data.split("_", 1)[1]
    rec = _ANON_GRANTS.pop(token, None)
    bot.answer_callback_query(c.id, "Confirmed \u2014 logging out from our side\u2026")
    if rec:
        try: rec["stop"].set()
        except Exception: pass
        _anon_logout(rec["acc"])
    safe_edit(c.message.chat.id, c.message.message_id,
              f"{pe('check')} <b>Thank you!</b>\n"
              f"{DIV}\n"
              f"{pe('shield')} Your account is fully yours now.\n"
              f"{pe('gem')} Enjoy! Need help? Contact support.",
              types.InlineKeyboardMarkup().add(IB("\U0001F3E0 Main Menu", callback_data="home")))


@bot.callback_query_handler(func=lambda c: c.data.startswith("anonlogn_"))
def cb_anon_log_no(c):
    token = c.data.split("_", 1)[1]
    rec = _ANON_GRANTS.pop(token, None)
    bot.answer_callback_query(c.id, "New login removed.")
    if rec:
        try: rec["stop"].set()
        except Exception: pass
        _anon_kill_new(rec["acc"], rec.get("new") or set())
    safe_edit(c.message.chat.id, c.message.message_id,
              f"{pe('cross')} <b>New Login Removed</b>\n"
              f"{DIV}\n"
              f"{pe('shield')} The newly detected login session has been removed.\n"
              f"{pe('bolt')} Please try logging in again.",
              types.InlineKeyboardMarkup().add(IB("\U0001F3E0 Main Menu", callback_data="home")))


@bot.message_handler(func=lambda m: True, content_types=["text"])
def fallback(m):
    ensure_user(m.from_user)
    if is_admin(m.from_user.id):
        return
    if maintenance_on():
        return safe_send(m.chat.id,
                         f"{pe('tool')} <b>Under Maintenance</b>\n{DIV}\n"
                         f"{pe('info')} We are upgrading the store right now.\n"
                         f"{pe('clock')} Please try again in a little while.")
    send_main_menu(m.chat.id, m.from_user)

# ═══════════════════════════════════════════════════════════════════════════
#  FLASK (keep-alive + payment webhook)
# ═══════════════════════════════════════════════════════════════════════════
def _web_store_html(u, key, api_base):
    """Self-contained browser storefront for a reseller: live balance + every
    available account, buyable right in the browser. No admin panel."""
    uname = esc(u.get("first_name") or "Reseller")
    uid = str(u.get("user_id"))
    bal = money(u.get("balance", 0))
    base = (api_base or "").rstrip("/")
    html = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__STORE__ &bull; Store</title>
<style>
:root{--gold:#f5c451;--gold2:#e0a83a;--bg:#0b0d12;--card:#141824;--line:#242a3a;--txt:#e8ecf4;--mut:#8b93a7;--green:#7ee787}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Inter,Arial,sans-serif;background:radial-gradient(1200px 600px at 50% -10%,#1b2233 0%,var(--bg) 60%);color:var(--txt);min-height:100vh}
.wrap{max-width:1000px;margin:0 auto;padding:28px 16px 70px}
.hero{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:22px}
.brand{font-size:24px;font-weight:800;background:linear-gradient(90deg,#fff,var(--gold));-webkit-background-clip:text;background-clip:text;color:transparent}
.sub{color:var(--mut);font-size:13px}
.balcard{background:linear-gradient(180deg,#161b29,#11151f);border:1px solid var(--line);border-radius:16px;padding:14px 20px;text-align:right}
.balcard .k{color:var(--mut);font-size:11px;letter-spacing:1px;text-transform:uppercase}
.balcard .v{font-size:24px;font-weight:800;color:var(--gold);margin-top:4px}
.tabs{display:flex;gap:8px;overflow-x:auto;padding-bottom:6px;margin:8px 0 18px}
.tab{white-space:nowrap;background:#141824;border:1px solid var(--line);color:var(--txt);border-radius:999px;padding:9px 16px;font-size:13px;cursor:pointer;transition:.15s}
.tab:hover{border-color:var(--gold2)}
.tab.active{background:linear-gradient(180deg,var(--gold),var(--gold2));color:#1a1400;border-color:var(--gold);font-weight:700}
.tab .cnt{opacity:.7;font-size:11px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:14px}
.acc{background:linear-gradient(180deg,#161b29,#11151f);border:1px solid var(--line);border-radius:16px;padding:16px;display:flex;flex-direction:column;gap:8px;transition:.15s}
.acc:hover{border-color:var(--gold2);transform:translateY(-2px)}
.acc-top{display:flex;justify-content:space-between;align-items:center}
.flag{font-size:13px;font-weight:700;color:#ffd98a;background:#0a0d14;border:1px solid var(--line);border-radius:8px;padding:3px 9px}
.price{font-size:18px;font-weight:800;color:var(--gold)}
.acc-name{font-size:15px;font-weight:600}
.acc-meta{color:var(--mut);font-size:12px}
.buy{margin-top:6px;background:linear-gradient(180deg,var(--gold),var(--gold2));border:0;color:#1a1400;font-weight:800;border-radius:12px;padding:11px;cursor:pointer;font-size:14px}
.buy:hover{filter:brightness(1.07)}
.empty{grid-column:1/-1;text-align:center;color:var(--mut);padding:50px 0;font-size:15px}
.overlay{position:fixed;inset:0;background:rgba(4,6,10,.82);display:none;align-items:center;justify-content:center;padding:18px;z-index:50}
.overlay.on{display:flex}
.modal{background:#12161f;border:1px solid var(--line);border-radius:18px;max-width:460px;width:100%;padding:22px;max-height:88vh;overflow:auto}
.modal h3{margin:0 0 14px;font-size:19px}
.row{display:flex;justify-content:space-between;gap:10px;padding:9px 0;border-bottom:1px solid var(--line);font-size:14px}
.row .k{color:var(--mut)}
.row .v{font-family:ui-monospace,Menlo,monospace;color:#ffd98a;word-break:break-all;text-align:right}
.btn{width:100%;margin-top:16px;background:linear-gradient(180deg,var(--gold),var(--gold2));border:0;color:#1a1400;font-weight:800;border-radius:12px;padding:13px;cursor:pointer;font-size:15px}
.btn.ghost{background:#1a2030;color:var(--txt);border:1px solid var(--line)}
.spin{text-align:center;color:var(--mut);padding:40px}
.foot{text-align:center;color:var(--mut);font-size:12px;margin-top:34px}
@media(max-width:560px){.hero{flex-direction:column;align-items:flex-start}.balcard{text-align:left;width:100%}}
</style></head><body>
<div class="wrap">
  <div class="hero">
    <div>
      <div class="brand">__STORE__</div>
      <div class="sub">Welcome, __USER__ &bull; Account ID #__UID__</div>
    </div>
    <div class="balcard"><div class="k">Wallet Balance</div><div class="v" id="bal">__BAL__</div></div>
  </div>
  <div class="tabs" id="tabs"></div>
  <div class="grid" id="grid"><div class="spin">Loading accounts&hellip;</div></div>
  <div class="foot">Live storefront &bull; Purchases are deducted from your balance instantly.<br>&copy; 2026 __STORE__</div>
</div>

<div class="overlay" id="ov"><div class="modal" id="modal"></div></div>

<script>
var API = "__API__";
var KEY = "__KEY__";
var CUR = "__CUR__";

function api(path, opts){
  var sep = path.indexOf("?") >= 0 ? "&" : "?";
  return fetch(API + path + sep + "api_key=" + encodeURIComponent(KEY), opts)
         .then(function(r){ return r.json(); });
}
function fmt(n){ return CUR + Number(n||0).toFixed(2); }
function el(id){ return document.getElementById(id); }

function loadBalance(){
  api("/balance").then(function(d){ if(d && d.ok){ el("bal").textContent = fmt(d.balance); } });
}
function loadCountries(){
  api("/countries").then(function(d){
    var tabs = el("tabs"); tabs.innerHTML = "";
    var all = document.createElement("button");
    all.className = "tab active"; all.textContent = "All Countries";
    all.onclick = function(){ selectCountry("", all); };
    tabs.appendChild(all);
    (d.countries||[]).forEach(function(c){
      var b = document.createElement("button");
      b.className = "tab";
      b.innerHTML = c.country + " <span class='cnt'>" + c.stock + "</span>";
      b.onclick = function(){ selectCountry(c.country, b); };
      tabs.appendChild(b);
    });
    selectCountry("", all);
  });
}
function selectCountry(country, btn){
  var tabs = document.querySelectorAll(".tab");
  for(var i=0;i<tabs.length;i++){ tabs[i].classList.remove("active"); }
  if(btn){ btn.classList.add("active"); }
  el("grid").innerHTML = "<div class='spin'>Loading&hellip;</div>";
  api("/accounts" + (country ? "?country=" + encodeURIComponent(country) : "")).then(function(d){
    renderAccounts(d.accounts||[]);
  });
}
function renderAccounts(list){
  var g = el("grid");
  if(!list.length){ g.innerHTML = "<div class='empty'>No accounts in stock right now. Please check back soon.</div>"; return; }
  g.innerHTML = list.map(function(a){
    return "<div class='acc'>" +
      "<div class='acc-top'><span class='flag'>" + (a.code||a.country) + "</span><span class='price'>" + fmt(a.price) + "</span></div>" +
      "<div class='acc-name'>" + a.country + "</div>" +
      "<div class='acc-meta'>Stock ID #" + a.id + "</div>" +
      "<button class='buy' data-id='" + a.id + "'>Buy Now</button></div>";
  }).join("");
  var btns = g.querySelectorAll(".buy");
  for(var i=0;i<btns.length;i++){
    btns[i].onclick = function(){ buy(Number(this.getAttribute("data-id"))); };
  }
}
function closeModal(){ el("ov").classList.remove("on"); }
function buy(id){
  el("modal").innerHTML = "<h3>Confirm Purchase</h3>" +
    "<div class='sub'>Buying stock #" + id + ". The price is deducted from your balance instantly.</div>" +
    "<button class='btn' id='yes'>Confirm &amp; Buy</button>" +
    "<button class='btn ghost' id='no'>Cancel</button>";
  el("ov").classList.add("on");
  el("no").onclick = closeModal;
  el("yes").onclick = function(){ doBuy(id); };
}
function doBuy(id){
  el("modal").innerHTML = "<div class='spin'>Processing your purchase&hellip;</div>";
  api("/buy", { method:"POST", headers:{"Content-Type":"application/json"},
                body: JSON.stringify({account_id:id}) })
  .then(function(d){
    if(d && d.ok){
      el("modal").innerHTML = "<h3>Purchase Complete</h3>" +
        "<div class='row'><span class='k'>Order</span><span class='v'>#" + d.order_id + "</span></div>" +
        "<div class='row'><span class='k'>Country</span><span class='v'>" + (d.country||"-") + "</span></div>" +
        "<div class='row'><span class='k'>Phone</span><span class='v'>" + (d.phone||"-") + "</span></div>" +
        "<div class='row'><span class='k'>Login Code</span><span class='v'>" + (d.code||"-") + "</span></div>" +
        "<div class='row'><span class='k'>API ID</span><span class='v'>" + (d.api_id||"-") + "</span></div>" +
        "<div class='row'><span class='k'>API Hash</span><span class='v'>" + (d.api_hash||"-") + "</span></div>" +
        "<div class='row'><span class='k'>Session</span><span class='v'>" + (d.session||"-") + "</span></div>" +
        "<div class='row'><span class='k'>New Balance</span><span class='v'>" + fmt(d.balance_after) + "</span></div>" +
        "<button class='btn' id='ok'>Done</button>";
      el("ok").onclick = closeModal;
      loadBalance(); loadCountries();
    } else {
      el("modal").innerHTML = "<h3>Purchase Failed</h3><div class='sub'>" +
        ((d && d.error) || "Unknown error") + "</div><button class='btn ghost' id='ok'>Close</button>";
      el("ok").onclick = closeModal;
    }
  }).catch(function(e){
    el("modal").innerHTML = "<h3>Network Error</h3><div class='sub'>" + e +
      "</div><button class='btn ghost' id='ok'>Close</button>";
    el("ok").onclick = closeModal;
  });
}
loadBalance();
loadCountries();
</script>
</body></html>"""
    return (html.replace("__STORE__", esc(STORE_NAME))
                .replace("__API__", base)
                .replace("__KEY__", str(key))
                .replace("__USER__", uname)
                .replace("__UID__", uid)
                .replace("__BAL__", bal)
                .replace("__CUR__", CURRENCY))


def start_flask():
    if not FLASK_OK:
        return
    app = Flask("store")

    def _api_dashboard_html(u, key):
        """Premium landing page shown when a reseller opens their personal API link."""
        bal = money(u.get("balance", 0))
        uid = u.get("user_id")
        uname = esc(u.get("first_name") or "Reseller")
        endpoints = [
            ("GET", "/api/v1/balance", "Your wallet balance"),
            ("GET", "/api/v1/countries", "Live stock grouped by country"),
            ("GET", "/api/v1/accounts?country=India", "Available accounts"),
            ("POST", "/api/v1/buy", "Buy an account (auto-deducts balance)"),
            ("GET", "/api/v1/orders", "Your API orders"),
            ("GET", "/api/v1/order/&lt;id&gt;", "Single order details"),
        ]
        rows = "".join(
            f"<tr><td class='m'>{m}</td><td><code>{esc(p)}</code></td><td>{esc(d)}</td></tr>"
            for m, p, d in endpoints)
        return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(STORE_NAME)} • API Dashboard</title>
<style>
:root{{--gold:#f5c451;--gold2:#e0a83a;--bg:#0b0d12;--card:#141824;--line:#242a3a;--txt:#e8ecf4;--mut:#8b93a7}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:-apple-system,Segoe UI,Roboto,Inter,Arial,sans-serif;background:
radial-gradient(1200px 600px at 50% -10%,#1b2233 0%,var(--bg) 60%);color:var(--txt);min-height:100vh}}
.wrap{{max-width:860px;margin:0 auto;padding:34px 18px 60px}}
.hero{{text-align:center;margin-bottom:26px}}
.badge{{display:inline-block;font-size:12px;letter-spacing:2px;color:var(--gold);
border:1px solid var(--gold);border-radius:999px;padding:6px 14px;text-transform:uppercase}}
h1{{font-size:30px;margin:16px 0 6px;background:linear-gradient(90deg,#fff,var(--gold));
-webkit-background-clip:text;background-clip:text;color:transparent}}
.sub{{color:var(--mut);font-size:14px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:22px 0}}
.card{{background:linear-gradient(180deg,#161b29,#11151f);border:1px solid var(--line);
border-radius:16px;padding:18px}}
.card .k{{color:var(--mut);font-size:12px;letter-spacing:1px;text-transform:uppercase}}
.card .v{{font-size:22px;font-weight:700;margin-top:6px}}
.card .v.gold{{color:var(--gold)}}
.keybox{{margin:18px 0;background:#0e121b;border:1px dashed var(--gold2);border-radius:14px;padding:16px}}
.keybox .k{{color:var(--mut);font-size:12px;letter-spacing:1px;text-transform:uppercase;margin-bottom:8px}}
code{{background:#0a0d14;border:1px solid var(--line);border-radius:8px;padding:2px 7px;
font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#ffd98a;font-size:13px;word-break:break-all}}
table{{width:100%;border-collapse:collapse;margin-top:8px;font-size:14px}}
th,td{{text-align:left;padding:11px 10px;border-bottom:1px solid var(--line)}}
th{{color:var(--mut);font-weight:600;font-size:12px;letter-spacing:1px;text-transform:uppercase}}
td.m{{color:#7ee787;font-weight:700;font-family:ui-monospace,monospace}}
.foot{{text-align:center;color:var(--mut);font-size:12px;margin-top:26px}}
.pill{{display:inline-block;background:#12231a;color:#7ee787;border:1px solid #1f4a30;
border-radius:999px;padding:4px 10px;font-size:12px}}
@media(max-width:560px){{.grid{{grid-template-columns:1fr}}h1{{font-size:23px}}}}
</style></head><body><div class="wrap">
<div class="hero">
<div class="badge">REST API v1 • Operational</div>
<h1>{esc(STORE_NAME)} Developer API</h1>
<div class="sub">Welcome back, {uname} • <span class="pill">● Live</span></div>
</div>
<div class="grid">
<div class="card"><div class="k">Account ID</div><div class="v">{uid}</div></div>
<div class="card"><div class="k">Wallet Balance</div><div class="v gold">{bal}</div></div>
</div>
<div class="keybox"><div class="k">Your Secret API Key</div><code>{esc(key)}</code></div>
<div class="card"><div class="k">Endpoints</div>
<table><thead><tr><th>Method</th><th>Path</th><th>Description</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<div class="foot">Send your key as the <code>X-API-Key</code> header (or <code>?api_key=</code>).<br>
Every purchase is deducted from your own balance. © 2026 {esc(STORE_NAME)}</div>
</div></body></html>"""

    @app.route("/")
    def home():
        return "QURESHI x OTP store bot is running."

    @app.route("/health")
    def health():
        return jsonify({"ok": True, "status": "alive"})

    @app.route("/pay/webhook", methods=["POST"])
    def pay_webhook():
        """Auto-credit hook. Configure your gateway to POST {utr, amount, status}."""
        try:
            data = flask_request.get_json(force=True, silent=True) or flask_request.form.to_dict()
            utr = str(data.get("utr", "")).strip()
            status = str(data.get("status", "")).lower()
            if utr and status in ("success", "paid", "completed"):
                with _db_lock:
                    con = db(); c = con.cursor()
                    c.execute("SELECT * FROM deposits WHERE utr=? AND status='pending'", (utr,))
                    d = c.fetchone()
                    if d:
                        d = dict(d)
                        c.execute("UPDATE deposits SET status='approved', credited_at=? WHERE id=?",
                                  (datetime.utcnow().isoformat(), d["id"]))
                        c.execute("UPDATE users SET balance = balance + ? WHERE user_id=?",
                                  (d["amount"], d["user_id"]))
                        con.commit()
                        try:
                            safe_send(d["user_id"],
                                      f"{pe('check')} <b>Payment auto-verified!</b> {money(d['amount'])} credited.")
                        except Exception:
                            pass
                    con.close()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}, 400

    # ── Developer REST API (auth: X-API-Key header or ?api_key=) ──
    def _api_user():
        key = (flask_request.headers.get("X-API-Key")
               or flask_request.args.get("api_key"))
        if not key:
            body = flask_request.get_json(force=True, silent=True) or {}
            key = body.get("api_key")
        return user_by_api_key(key)

    @app.route("/store", methods=["GET"])
    def web_store():
        key = (flask_request.args.get("api_key")
               or flask_request.headers.get("X-API-Key"))
        u = user_by_api_key(key) if key else None
        if not u:
            return ("<html><body style='font-family:sans-serif;background:#0b0d12;"
                    "color:#e8ecf4;text-align:center;padding:70px 20px'>"
                    "<h2>Invalid or missing API key</h2>"
                    "<p style='color:#8b93a7'>Open your personal store link from the bot to continue.</p>"
                    "</body></html>"), 401
        base = get_api_base()
        return _web_store_html(u, key, base)

    @app.route("/api/v1", methods=["GET"])
    def api_root():
        key = flask_request.args.get("api_key")
        u = user_by_api_key(key) if key else None
        if u:
            return _api_dashboard_html(u, key)
        return jsonify({"ok": True, "service": STORE_NAME + " REST API", "version": "v1",
                        "endpoints": ["/api/v1/balance", "/api/v1/countries",
                                      "/api/v1/accounts?country=India", "/api/v1/buy",
                                      "/api/v1/orders", "/api/v1/order/<id>"]})

    @app.route("/api/v1/balance", methods=["GET"])
    def api_balance():
        u = _api_user()
        if not u:
            return jsonify({"ok": False, "error": "invalid api key"}), 401
        return jsonify({"ok": True, "user_id": u["user_id"],
                        "balance": float(u.get("balance", 0) or 0), "currency": CURRENCY})

    @app.route("/api/v1/countries", methods=["GET"])
    def api_countries():
        u = _api_user()
        if not u:
            return jsonify({"ok": False, "error": "invalid api key"}), 401
        with _db_lock:
            con = db(); c = con.cursor()
            c.execute("""SELECT country, cc, code, COUNT(*) n, MIN(price) minp
                         FROM accounts WHERE status='available'
                         GROUP BY country ORDER BY country""")
            rows = [dict(r) for r in c.fetchall()]; con.close()
        return jsonify({"ok": True, "countries": [
            {"country": r["country"], "cc": r["cc"], "code": r["code"],
             "stock": r["n"], "min_price": float(r["minp"] or 0)} for r in rows]})

    @app.route("/api/v1/accounts", methods=["GET"])
    def api_accounts():
        u = _api_user()
        if not u:
            return jsonify({"ok": False, "error": "invalid api key"}), 401
        country = flask_request.args.get("country")
        try:
            limit = min(int(flask_request.args.get("limit", 50) or 50), 200)
        except Exception:
            limit = 50
        with _db_lock:
            con = db(); c = con.cursor()
            if country:
                c.execute("""SELECT id,country,cc,code,price FROM accounts
                             WHERE status='available' AND country=?
                             ORDER BY price ASC LIMIT ?""", (country, limit))
            else:
                c.execute("""SELECT id,country,cc,code,price FROM accounts
                             WHERE status='available' ORDER BY price ASC LIMIT ?""", (limit,))
            rows = [dict(r) for r in c.fetchall()]; con.close()
        return jsonify({"ok": True, "count": len(rows), "accounts": rows})

    @app.route("/api/v1/buy", methods=["POST"])
    def api_buy():
        u = _api_user()
        if not u:
            return jsonify({"ok": False, "error": "invalid api key"}), 401
        data = flask_request.get_json(force=True, silent=True) or flask_request.form.to_dict()
        uid = u["user_id"]
        aid = None
        try:
            if data.get("account_id") or data.get("id"):
                aid = int(data.get("account_id") or data.get("id"))
        except Exception:
            aid = None
        country = (data.get("country") or "").strip()
        with _db_lock:
            con = db(); c = con.cursor()
            if aid:
                c.execute("SELECT * FROM accounts WHERE id=?", (aid,))
            elif country:
                c.execute("""SELECT * FROM accounts WHERE status='available'
                             AND (LOWER(country)=LOWER(?) OR LOWER(code)=LOWER(?) OR UPPER(cc)=UPPER(?))
                             ORDER BY price ASC LIMIT 1""",
                          (country, country, country))
            else:
                c.execute("SELECT * FROM accounts WHERE status='available' ORDER BY price ASC LIMIT 1")
            acc = c.fetchone()
            if not acc:
                con.close()
                return jsonify({"ok": False, "error": "out of stock", "country": country}), 404
            acc = dict(acc)
            if acc["status"] != "available":
                con.close(); return jsonify({"ok": False, "error": "account not available"}), 409
            aid = acc["id"]
            c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
            row = c.fetchone()
            bal = float((row["balance"] if row else 0) or 0)
            price = float(acc["price"] or 0)
            if bal < price:
                con.close()
                return jsonify({"ok": False, "error": "insufficient balance",
                                "balance": bal, "price": price}), 402
            c.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (price, uid))
            c.execute("UPDATE accounts SET status='sold', sold_to=?, sold_at=? WHERE id=?",
                      (uid, datetime.utcnow().isoformat(), aid))
            c.execute("""INSERT INTO orders(user_id,account_id,price,status,created_at,updated_at)
                         VALUES(?,?,?,?,?,?)""",
                      (uid, aid, price, "completed",
                       datetime.utcnow().isoformat(), datetime.utcnow().isoformat()))
            oid = c.lastrowid
            con.commit(); con.close()
        try:
            safe_send(uid, f"{pe('check')} <b>API purchase complete</b>\n{DIV}\n"
                           f"{pe('box')} Order #{oid} \u2022 {money(price)}\n"
                           f"{pe('globe')} {esc(acc['country'])} \u2022 <code>{esc(acc['code'])}</code>")
        except Exception:
            pass
        return jsonify({"ok": True, "order_id": oid, "account_id": aid,
                        "phone": acc["phone"], "country": acc["country"],
                        "cc": acc["cc"], "code": acc["code"], "price": price,
                        "session": acc["session"], "api_id": acc["api_id"],
                        "api_hash": acc["api_hash"], "balance_after": bal - price})

    @app.route("/api/v1/orders", methods=["GET"])
    def api_orders():
        u = _api_user()
        if not u:
            return jsonify({"ok": False, "error": "invalid api key"}), 401
        with _db_lock:
            con = db(); c = con.cursor()
            c.execute("""SELECT o.id,o.account_id,o.price,o.status,o.otp,o.created_at,
                                a.country,a.cc,a.code,a.phone
                         FROM orders o LEFT JOIN accounts a ON a.id=o.account_id
                         WHERE o.user_id=? ORDER BY o.id DESC LIMIT 100""", (u["user_id"],))
            rows = [dict(r) for r in c.fetchall()]; con.close()
        return jsonify({"ok": True, "count": len(rows), "orders": rows})

    @app.route("/api/v1/order/<int:oid>", methods=["GET"])
    def api_order(oid):
        u = _api_user()
        if not u:
            return jsonify({"ok": False, "error": "invalid api key"}), 401
        with _db_lock:
            con = db(); c = con.cursor()
            c.execute("""SELECT o.*, a.country,a.cc,a.code,a.phone,a.session,a.api_id,a.api_hash
                         FROM orders o LEFT JOIN accounts a ON a.id=o.account_id
                         WHERE o.id=? AND o.user_id=?""", (oid, u["user_id"]))
            r = c.fetchone(); con.close()
        if not r:
            return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify({"ok": True, "order": dict(r)})

    def _run():
        # Retry binding: on some hosts the port is briefly busy at boot.
        for _attempt in range(1, 6):
            try:
                app.run(host="0.0.0.0", port=PORT, use_reloader=False, threaded=True)
                return
            except Exception as e:
                log.warning("flask bind failed on port %s (attempt %s): %s",
                            PORT, _attempt, e)
                time.sleep(3)
    threading.Thread(target=_run, daemon=True, name="flask").start()

# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════
# \u2500\u2500 PUBLIC TUNNEL (clean, non-branded public URL for the Developer API) \u2500\u2500
def _find_cloudflared():
    """Locate the cloudflared binary (PATH, next to this file, or ./)."""
    import shutil
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in ("cloudflared", os.path.join(here, "cloudflared"),
                 os.path.join(os.getcwd(), "cloudflared")):
        if os.path.sep in cand:
            if os.path.exists(cand) and os.access(cand, os.X_OK):
                return cand
        else:
            w = shutil.which(cand)
            if w:
                return w
    return None


def _download_cloudflared(dest):
    """Best-effort: fetch the cloudflared binary if it is missing (Linux amd64)."""
    import urllib.request, stat
    url = ("https://github.com/cloudflare/cloudflared/releases/latest/download/"
           "cloudflared-linux-amd64")
    try:
        log.info("Downloading cloudflared\u2026")
        with urllib.request.urlopen(url, timeout=20) as _r, open(dest, "wb") as _f:
            while True:
                _chunk = _r.read(262144)
                if not _chunk:
                    break
                _f.write(_chunk)
        os.chmod(dest, os.stat(dest).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return os.path.exists(dest)
    except Exception as e:
        log.warning("cloudflared download failed: %s", e)
        return False


def _try_localhost_run():
    """Fallback public tunnel via localhost.run (SSH reverse tunnel).

    No account and no Cloudflare rate-limit. Prints a public https URL that we
    capture and store as the API base. Returns True if a URL was obtained."""
    import subprocess
    try:
        proc = subprocess.Popen(
            ["ssh", "-o", "StrictHostKeyChecking=no",
             "-o", "UserKnownHostsFile=/dev/null",
             "-o", "ServerAliveInterval=30", "-o", "ExitOnForwardFailure=yes",
             "-R", f"80:localhost:{PORT}", "nokey@localhost.run"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            start_new_session=True)
        for line in proc.stdout:
            m = re.search(r"https://[A-Za-z0-9.-]+\.lhr\.life", line or "")
            if m:
                set_setting("api_base_url", m.group(0).rstrip("/") + "/api/v1")
                log.info("Public API endpoint (localhost.run): %s", get_api_base())
                # clean up any leftover failed cloudflared attempts
                try:
                    subprocess.run(["pkill", "-f", "cloudflared tunnel"], check=False,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass
                for _ in proc.stdout:
                    pass
                return True
        try:
            os.killpg(os.getpgid(proc.pid), 9)
        except Exception:
            pass
    except Exception as e:
        log.warning("localhost.run fallback failed: %s", e)
    return False


def start_public_tunnel():
    """Expose the REST API on a clean public URL using a cloudflared quick tunnel.
    Auto-updates the api_base_url setting so resellers always get a live endpoint.

    Priority:
      1. PUBLIC_BASE_URL env (your own Cloudflare domain / named tunnel) - used as-is.
      2. CLOUDFLARED_TOKEN env (a named Cloudflare tunnel) - `cloudflared tunnel run`.
      3. Quick tunnel (random *.trycloudflare.com) - zero-config, no domain needed.
    Set PUBLIC_TUNNEL=0 to disable entirely."""
    if os.environ.get("PUBLIC_TUNNEL", "1") == "0":
        return

    # 1) own domain supplied by the operator
    env_base = _valid_public_base(os.environ.get("PUBLIC_BASE_URL", ""))
    if env_base:
        set_setting("api_base_url", env_base if env_base.endswith("/api/v1")
                    else env_base + "/api/v1")
        log.info("Public API endpoint (own domain): %s", get_api_base())
        return

    # clear any stale placeholder so the UI never shows 'your-domain'
    if not _valid_public_base(get_setting("api_base_url", "")):
        set_setting("api_base_url", "")

    token = os.environ.get("CLOUDFLARED_TOKEN", "").strip()

    def _do():
        import time as _t
        import subprocess
        # Resolve cloudflared INSIDE this background thread so a slow or
        # blocked download can never delay / hang bot startup on hosting.
        try:
            subprocess.run(["pkill", "-f", "cloudflared tunnel"], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["pkill", "-f", "localhost.run"], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        cf = _find_cloudflared()
        if not cf:
            dest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloudflared")
            if _download_cloudflared(dest):
                cf = dest
        if not cf:
            log.info("cloudflared not found - Developer API will show 'provisioning\u2026'")
            return
        attempt = 0
        while attempt < 3:
            attempt += 1
            proc = None
            try:
                if token:
                    cmd = [cf, "tunnel", "--no-autoupdate", "run", "--token", token]
                else:
                    cmd = [cf, "tunnel", "--url", f"http://localhost:{PORT}",
                           "--no-autoupdate"]
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, bufsize=1,
                                        start_new_session=True)
                url = None
                for line in proc.stdout:
                    m = re.search(r"https://[A-Za-z0-9.-]+\.trycloudflare\.com", line or "")
                    if m:
                        url = m.group(0)
                        break
                    if token and "Registered tunnel connection" in (line or ""):
                        break  # named tunnel: URL comes from PUBLIC_BASE_URL
                if url:
                    set_setting("api_base_url", url.rstrip("/") + "/api/v1")
                    log.info("Public API endpoint: %s", get_api_base())
                    # ensure ONLY this tunnel stays alive (kill any other cloudflared)
                    try:
                        _out = subprocess.run(["pgrep", "-f", "cloudflared tunnel"],
                                              capture_output=True, text=True).stdout
                        for _p in (_out or "").split():
                            try:
                                if _p and int(_p) != proc.pid:
                                    os.kill(int(_p), 9)
                            except Exception:
                                pass
                    except Exception:
                        pass
                    # keep draining the pipe so this one tunnel keeps running
                    for _ in proc.stdout:
                        pass
                    return
                # no URL captured -> kill this attempt's whole group (avoid orphans)
                try:
                    os.killpg(os.getpgid(proc.pid), 9)
                except Exception:
                    pass
            except Exception as e:
                log.warning("public tunnel attempt %s failed: %s", attempt, e)
                try:
                    if proc:
                        os.killpg(os.getpgid(proc.pid), 9)
                except Exception:
                    pass
            _t.sleep(15)
        log.warning("public tunnel gave up after %s attempts", attempt)
        # Cloudflare quick tunnels are rate-limited (HTTP 429); fall back to an
        # SSH reverse tunnel (localhost.run) which needs no account.
        _try_localhost_run()

    threading.Thread(target=_do, daemon=True).start()


def start_wa_bridge():
    """Auto-start the WhatsApp (Baileys) pairing bridge if it isn't already up.

    This prevents the 'Connection refused [Errno 111]' error that appears when
    the admin tries to add a WhatsApp account but the bridge service is down."""
    try:
        import socket, subprocess, shutil
        host, port = "127.0.0.1", 8790
        try:
            from urllib.parse import urlparse as _up
            _u = _up(WA_BRIDGE)
            host = _u.hostname or host
            port = _u.port or port
        except Exception:
            pass
        # already listening?
        _s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); _s.settimeout(1.0)
        try:
            _s.connect((host, port)); _s.close()
            log.info("WhatsApp bridge already running on %s:%s", host, port)
            return
        except Exception:
            pass
        finally:
            try: _s.close()
            except Exception: pass
        node = shutil.which("node")
        if not node:
            log.warning("node not found \u2014 WhatsApp bridge will not auto-start")
            return
        bridge_dir = os.path.join(BASE_DIR, "wa_bridge")
        entry = os.path.join(bridge_dir, "index.js")
        if not os.path.exists(entry):
            log.warning("wa_bridge/index.js not found \u2014 WhatsApp bridge will not auto-start")
            return
        # install deps on first run (background, one-time)
        if not os.path.isdir(os.path.join(bridge_dir, "node_modules")):
            npm = shutil.which("npm")
            if npm:
                log.info("Installing WhatsApp bridge dependencies (first run)\u2026")
                try:
                    with open(os.path.join(bridge_dir, "npm_install.log"), "a") as _lf:
                        subprocess.Popen([npm, "install"], cwd=bridge_dir,
                                         stdout=_lf, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, start_new_session=True)
                except Exception as e:
                    log.warning("npm install failed to start: %s", e)
        env = dict(os.environ); env.setdefault("PORT", str(port))
        try:
            _logf = open(os.path.join(bridge_dir, "bridge.log"), "a")
        except Exception:
            _logf = subprocess.DEVNULL
        subprocess.Popen([node, "index.js"], cwd=bridge_dir, env=env,
                         stdout=_logf, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        log.info("Starting WhatsApp bridge on %s:%s \u2026", host, port)
    except Exception as e:
        log.warning("start_wa_bridge failed: %s", e)


def _start_polling_thread():
    """Run Telegram long-polling in a daemon thread so the MAIN thread can act
    as an unkillable supervisor — this is what keeps the bot alive 24/7."""
    def _poll_loop():
        while True:
            try:
                bot.infinity_polling(timeout=30, long_polling_timeout=25)
            except Exception as e:
                log.error("polling crashed: %s — restarting in 5s", e)
                time.sleep(5)
            except BaseException as e:  # never let the thread die silently
                log.error("polling fatal: %s — restarting in 10s", e)
                time.sleep(10)
    t = threading.Thread(target=_poll_loop, daemon=True, name="polling")
    t.start()
    return t


def start_keepalive():
    """Ping our own public URL every few minutes so free hosts (Render / Koyeb)
    do not put the service to sleep. Override the target with SELF_URL."""
    def _loop():
        import urllib.request
        while True:
            time.sleep(240)
            url = os.environ.get("SELF_URL", "").strip() or get_public_root()
            if not url:
                continue
            try:
                urllib.request.urlopen(url, timeout=15).read(64)
            except Exception:
                pass
    threading.Thread(target=_loop, daemon=True, name="keepalive").start()


def main():
    if not BOT_TOKEN:
        print("\n" + "=" * 62)
        print("  BOT_TOKEN khaali hai \u2014 bot start nahi ho sakta.")
        print("  Fix:  export BOT_TOKEN='123456:ABC...'")
        print("     ya app_2.py ke CONFIG section me BOT_TOKEN bhar do.")
        print("=" * 62 + "\n")
        raise SystemExit(1)
    # Log uncaught exceptions from ANY thread (turns silent deaths into logs).
    try:
        def _thread_hook(args):
            log.error("uncaught thread exception (%s): %s",
                      getattr(args.thread, "name", "?"), args.exc_value,
                      exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        threading.excepthook = _thread_hook
    except Exception:
        pass

    # 1) boot the essentials — a failure here must NOT kill the process
    try:
        init_db()
        load_configs()
        log.info("DB ready at %s", DB_PATH)
    except Exception as e:
        log.error("startup (db/config) failed: %s", e, exc_info=True)

    # 2) optional services (web API / WhatsApp bridge / public tunnel / keepalive)
    for _fn in (start_flask, start_wa_bridge, start_public_tunnel, start_keepalive):
        try:
            _fn()
        except Exception as _e:
            log.warning("%s failed: %s", getattr(_fn, "__name__", _fn), _e)

    # 3) identify the bot
    try:
        # drop any stale updates queued from a previous bot on this token
        bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        log.warning("delete_webhook: %s", e)
    try:
        me = bot.get_me()
        global BOT_USERNAME
        BOT_USERNAME = me.username or BOT_USERNAME
        set_setting("bot_username", BOT_USERNAME)
        log.info("Bot online: @%s (%s)", me.username, me.first_name)
    except Exception as e:
        log.error("getMe failed: %s", e)

    # 4) start polling in a background thread
    log.info("Starting polling…")
    poll_thread = _start_polling_thread()

    # 5) MAIN THREAD = supervisor: never exits, restarts polling if it dies,
    #    and logs a heartbeat so the host always sees the process alive.
    _hb = 0
    while True:
        time.sleep(30)
        _hb += 1
        if not poll_thread.is_alive():
            log.warning("polling thread is dead — restarting it")
            poll_thread = _start_polling_thread()
        if _hb % 10 == 0:            # ~ every 5 minutes
            log.info("heartbeat: bot alive (tick %s)", _hb)

def phone_cli(argv=None):
    """Standalone 'Phone VPS' self-test — does NOT start the bot.

    Usage:
        python3 account_store_bot.py phone-test [HOST:PORT]
        python3 account_store_bot.py phone-otp  <package>
    """
    import sys as _sys
    argv = list(argv if argv is not None else _sys.argv[1:])
    pb = _phone()
    if pb is None:
        print("Phone bridge unavailable.")
        return 2
    if argv and argv[0] == "phone-test":
        if len(argv) > 1:
            print("connect       :", pb.connect(argv[1]))
        print("adb_available :", pb.adb_available())
        print("online        :", pb.online_devices())
        print("ok            :", pb.ok())
        for fn in ("model", "android_version"):
            try:
                print(f"{fn:14}:", getattr(pb, fn)())
            except Exception as e:
                print(f"{fn:14}: err {e}")
        for pkg in ("org.telegram.messenger", "com.whatsapp"):
            try:
                print(f"has {pkg:26}:", pb.has_package(pkg))
            except Exception as e:
                print(f"has {pkg:26}: err {e}")
        return 0
    if argv and argv[0] == "phone-otp" and len(argv) > 1:
        print("OTP:", pb.read_otp(argv[1]))
        return 0
    print("usage: python3 account_store_bot.py phone-test [HOST:PORT]")
    print("       python3 account_store_bot.py phone-otp <package>")
    return 1

if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) > 1 and _sys.argv[1].startswith("phone-"):
        raise SystemExit(phone_cli(_sys.argv[1:]))
    # Outer safety net: even if the whole runtime crashes, restart forever.
    while True:
        try:
            main()
        except KeyboardInterrupt:
            break
        except SystemExit:
            raise
        except BaseException as _e:
            log.error("FATAL runtime error: %s — restarting in 5s", _e, exc_info=True)
            time.sleep(5)
