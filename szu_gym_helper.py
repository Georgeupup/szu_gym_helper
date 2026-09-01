import requests
import time
import datetime
import json
import re
import urllib3
import tkinter as tk
from tkinter import messagebox
import threading
from pathlib import Path

# 禁用 HTTPS 证书警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= 核心配置区 =================
# 默认 Cookie，可在此处填入作为默认值，也可在界面上手动输入
DEFAULT_COOKIE = ""
DEFAULT_STUDENT_ID = ""
DEFAULT_STUDENT_NAME = ""

# API 接口地址
LIST_URL = "https://ehall.szu.edu.cn/qljfwapp/sys/lwSzuCgyy/sportVenue/getTimeList.do"
POST_URL = "https://ehall.szu.edu.cn/qljfwapp/sys/lwSzuCgyy/sportVenue/insertVenueBookingInfo.do"
SPORT_VENUE_URL = "https://ehall.szu.edu.cn/qljfwapp/sys/lwSzuCgyy/index.do#/sportVenue"
APP_INDEX_URL = "https://ehall.szu.edu.cn/qljfwapp/sys/lwSzuCgyy/index.do"
MY_BOOKING_URL = "https://ehall.szu.edu.cn/qljfwapp/sys/lwSzuCgyy/index.do#/myBooking"
BROWSER_COOKIE_DOMAIN = "ehall.szu.edu.cn"
BROWSER_COOKIE_PATH = "/qljfwapp/sys/lwSzuCgyy/sportVenue/getTimeList.do"
REQUIRED_AUTH_COOKIE_NAME = "MOD_AUTH_CAS"
PLAYWRIGHT_PROFILE_DIR = Path(__file__).with_name(".playwright_profile")
COOKIE_WAIT_SECONDS = 180

# 基础请求头
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://ehall.szu.edu.cn",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.6668.101 Safari/537.36",
    "Cookie": DEFAULT_COOKIE
}


def build_cookie_header(cookies):
    matched_cookies = []
    for cookie in cookies:
        domain = cookie.get("domain", "").lstrip(".")
        path = cookie.get("path") or "/"
        domain_match = (
            domain == BROWSER_COOKIE_DOMAIN
            or BROWSER_COOKIE_DOMAIN.endswith("." + domain)
            or domain.endswith(".szu.edu.cn")
        )
        path_match = BROWSER_COOKIE_PATH.startswith(path)
        if domain_match and path_match:
            matched_cookies.append(cookie)

    matched_cookies.sort(key=lambda item: len(item.get("path") or "/"), reverse=True)
    return "; ".join(f"{cookie['name']}={cookie['value']}" for cookie in matched_cookies)


def parse_cookie_header(cookie_header):
    cookies = []
    for item in cookie_header.split(";"):
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name:
            cookies.append({
                "name": name,
                "value": value,
                "domain": BROWSER_COOKIE_DOMAIN,
                "path": "/",
                "secure": True,
            })
    return cookies


def extract_user_info_from_html(html):
    match = re.search(r"USER_INFO\s*=\s*(\{.*?\})\s*;", html, re.DOTALL)
    user_info = {}
    if match:
        try:
            user_info = json.loads(match.group(1))
        except json.JSONDecodeError:
            user_info = {}

    info_values = user_info.get("info") or []
    user_id = str(
        user_info.get("id")
        or (info_values[0] if len(info_values) > 0 else "")
        or ""
    ).strip()
    user_name = str(
        user_info.get("name")
        or user_info.get("userName")
        or (info_values[1] if len(info_values) > 1 else "")
        or ""
    ).strip()

    if not user_id:
        id_match = re.search(r"USERID\s*=\s*['\"]([^'\"]+)['\"]", html)
        if id_match:
            user_id = id_match.group(1).strip()

    if not user_id or not user_name:
        raise RuntimeError("场馆页面未返回当前登录人的学号/工号和姓名")
    return user_id, user_name


def fetch_logged_in_user(cookie_header):
    headers = {**HEADERS, "Cookie": cookie_header}
    response = requests.get(
        APP_INDEX_URL,
        headers=headers,
        verify=False,
        timeout=8,
        allow_redirects=True,
    )
    if "authserver.szu.edu.cn" in response.url:
        raise RuntimeError("Cookie 已失效，请重新点击“自动获取”并登录")
    return extract_user_info_from_html(response.text)


def launch_persistent_browser(playwright):
    launch_options = [
        ("Edge", {"channel": "msedge"}),
        ("Chrome", {"channel": "chrome"}),
        ("Chromium", {}),
    ]
    errors = []

    for browser_name, browser_options in launch_options:
        try:
            context = playwright.chromium.launch_persistent_context(
                str(PLAYWRIGHT_PROFILE_DIR),
                headless=False,
                viewport={"width": 1280, "height": 900},
                args=["--start-maximized"],
                **browser_options
            )
            return browser_name, context
        except Exception as exc:
            errors.append(f"{browser_name}: {exc}")

    raise RuntimeError("\n".join(errors))


def load_cookie_from_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("缺少依赖 playwright，请先运行：pip install playwright") from exc

    PLAYWRIGHT_PROFILE_DIR.mkdir(exist_ok=True)

    with sync_playwright() as p:
        context = None
        try:
            browser_name, context = launch_persistent_browser(p)
            # 专用浏览器会保留上一次的登录态。先全部清除，避免程序在用户
            # 切换账号前就把旧账号的认证 Cookie 当作新 Cookie 返回。
            context.clear_cookies()
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(SPORT_VENUE_URL, wait_until="domcontentloaded", timeout=60000)

            deadline = time.time() + COOKIE_WAIT_SECONDS
            login_account = ""
            while time.time() < deadline:
                if "authserver.szu.edu.cn" in page.url:
                    try:
                        account_input = page.locator(
                            "input[name='username'], input#username"
                        ).first
                        current_account = account_input.input_value(timeout=200).strip()
                        if current_account:
                            login_account = current_account
                    except Exception:
                        pass

                cookies = context.cookies(LIST_URL)
                cookie_text = build_cookie_header(cookies)
                cookie_names = {cookie.get("name") for cookie in cookies}
                is_venue_page = page.url.startswith(
                    "https://ehall.szu.edu.cn/qljfwapp/sys/lwSzuCgyy/"
                )
                if (
                    cookie_text
                    and REQUIRED_AUTH_COOKIE_NAME in cookie_names
                    and is_venue_page
                ):
                    try:
                        user_data = page.evaluate(
                            """() => {
                                const info = window.USER_INFO || {};
                                const values = Array.isArray(info.info) ? info.info : [];
                                return {
                                    id: String(info.id || window.USERID || values[0] || ''),
                                    name: String(info.name || info.userName || values[1] || '')
                                };
                            }"""
                        )
                        user_id = user_data.get("id", "").strip()
                        user_name = user_data.get("name", "").strip()
                        if user_id and user_name:
                            return browser_name, cookie_text, user_id, user_name, login_account
                    except Exception:
                        # 登录跳转期间执行上下文可能被刷新，等待下一轮即可。
                        pass
                page.wait_for_timeout(1000)

            raise RuntimeError("等待登录超时，请在打开的浏览器中完成登录并进入体育场馆预约页面")
        except Exception as exc:
            raise RuntimeError(f"自动获取 Cookie 失败。\n{exc}") from exc
        finally:
            if context is not None:
                context.close()


def open_my_booking_page(cookie_header, opened_callback=None):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("缺少依赖 playwright，请先运行：pip install playwright==1.40.0") from exc

    cookies = parse_cookie_header(cookie_header)
    if not cookies:
        raise RuntimeError("当前 Cookie 为空，无法打开支付页面")

    PLAYWRIGHT_PROFILE_DIR.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser_name, context = launch_persistent_browser(p)
        try:
            context.add_cookies(cookies)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(MY_BOOKING_URL, wait_until="domcontentloaded", timeout=60000)
            if opened_callback is not None:
                opened_callback(browser_name)
            while context.pages:
                try:
                    page.wait_for_timeout(1000)
                except Exception:
                    break
            return browser_name
        finally:
            context.close()


class SniperGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("深大体育馆自动捡漏器 v2.4.1")
        self.root.geometry("760x720")
        self.root.minsize(720, 650)
        self.root.configure(bg="#F4F7FB")

        self.stop_event = threading.Event()
        self.is_running = False
        self.verified_cookie = ""
        self.logged_in_user = None
        self.login_account = ""

        self.colors = {
            "bg": "#F4F7FB",
            "panel": "#FFFFFF",
            "border": "#DCE3EC",
            "text": "#1F2937",
            "muted": "#6B7280",
            "primary": "#2563EB",
            "success": "#16A34A",
            "danger": "#DC2626",
            "warning": "#F59E0B",
            "disabled": "#E5E7EB",
        }

        header = tk.Frame(self.root, bg=self.colors["primary"], height=86)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(
            header,
            text="深大体育馆自动捡漏器",
            bg=self.colors["primary"],
            fg="white",
            font=("Microsoft YaHei UI", 20, "bold"),
        ).pack(anchor=tk.W, padx=24, pady=(16, 0))
        tk.Label(
            header,
            text="自动获取 Cookie，拉取场馆状态，选中时段后后台持续捡漏",
            bg=self.colors["primary"],
            fg="#DBEAFE",
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor=tk.W, padx=26, pady=(2, 0))

        content = tk.Frame(self.root, bg=self.colors["bg"])
        content.pack(fill=tk.BOTH, expand=True, padx=18, pady=16)

        # --- 身份认证区 ---
        frame_token = self.create_panel(content, "身份认证")
        frame_token.pack(fill=tk.X, pady=(0, 12))

        tk.Label(
            frame_token,
            text="Cookie / Token",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side=tk.LEFT, padx=(14, 8), pady=14)

        self.token_var = tk.StringVar(value=DEFAULT_COOKIE)
        self.token_entry = tk.Entry(
            frame_token,
            textvariable=self.token_var,
            width=58,
            relief=tk.FLAT,
            bg="#F9FAFB",
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            font=("Consolas", 10),
        )
        self.token_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10), ipady=7)
        self.btn_auto_cookie = self.make_button(
            frame_token, "自动获取", self.auto_fill_cookie, self.colors["primary"], width=10
        )
        self.btn_auto_cookie.pack(side=tk.LEFT, padx=(0, 14), pady=10)

        # --- 预约人信息区 ---
        frame_user = self.create_panel(content, "当前登录人（自动读取）")
        frame_user.pack(fill=tk.X, pady=(0, 12))

        user_fields = tk.Frame(frame_user, bg=self.colors["panel"])
        user_fields.pack(fill=tk.X, padx=14, pady=(10, 14))

        tk.Label(
            user_fields,
            text="学号 / 工号",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side=tk.LEFT, padx=(0, 8))

        self.student_id_var = tk.StringVar(value=DEFAULT_STUDENT_ID)
        self.student_id_entry = tk.Entry(
            user_fields,
            textvariable=self.student_id_var,
            width=18,
            relief=tk.FLAT,
            bg="#F9FAFB",
            fg=self.colors["text"],
            readonlybackground="#F3F4F6",
            font=("Consolas", 10),
            state="readonly",
        )
        self.student_id_entry.pack(side=tk.LEFT, padx=(0, 18), ipady=7)

        tk.Label(
            user_fields,
            text="姓名",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side=tk.LEFT, padx=(0, 8))

        self.student_name_var = tk.StringVar(value=DEFAULT_STUDENT_NAME)
        self.student_name_entry = tk.Entry(
            user_fields,
            textvariable=self.student_name_var,
            width=16,
            relief=tk.FLAT,
            bg="#F9FAFB",
            fg=self.colors["text"],
            readonlybackground="#F3F4F6",
            font=("Microsoft YaHei UI", 10),
            state="readonly",
        )
        self.student_name_entry.pack(side=tk.LEFT, ipady=7)

        # --- 预约操作区 ---
        frame_ops_outer = self.create_panel(content, "预约操作")
        frame_ops_outer.pack(fill=tk.X, pady=(0, 12))

        frame_date = tk.Frame(frame_ops_outer, bg=self.colors["panel"])
        frame_date.pack(fill=tk.X, padx=14, pady=(12, 8))

        tk.Label(
            frame_date,
            text="预约日期",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side=tk.LEFT, padx=(0, 8))

        self.date_var = tk.StringVar(value=datetime.date.today().strftime("%Y-%m-%d"))
        self.date_entry = tk.Entry(
            frame_date,
            textvariable=self.date_var,
            width=14,
            relief=tk.FLAT,
            bg="#F9FAFB",
            fg=self.colors["text"],
            justify=tk.CENTER,
            font=("Consolas", 11),
        )
        self.date_entry.pack(side=tk.LEFT, ipady=7)
        tk.Label(
            frame_date,
            text="格式 YYYY-MM-DD",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT, padx=10)

        frame_ops = tk.Frame(frame_ops_outer, bg=self.colors["panel"])
        frame_ops.pack(fill=tk.X, padx=14, pady=(0, 14))

        self.btn_fetch = self.make_button(frame_ops, "拉取场馆状态", self.fetch_slots, self.colors["success"], width=16)
        self.btn_fetch.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_stop = self.make_button(
            frame_ops, "停止当前捡漏", self.stop_sniping, self.colors["danger"], width=16, state=tk.DISABLED
        )
        self.btn_stop.pack(side=tk.LEFT)

        # --- 时段区 ---
        slots_panel = self.create_panel(content, "场馆时段")
        slots_panel.pack(fill=tk.BOTH, expand=True, pady=(0, 12))
        self.btn_frame = tk.Frame(slots_panel, bg=self.colors["panel"])
        self.btn_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)

        self.empty_slot_label = tk.Label(
            self.btn_frame,
            text="点击“拉取场馆状态”后会在这里显示可预约时段",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Microsoft YaHei UI", 11),
        )
        self.empty_slot_label.pack(expand=True)

        # --- 日志输出区 ---
        log_panel = self.create_panel(content, "运行日志")
        log_panel.pack(fill=tk.BOTH)
        self.log_text = tk.Text(
            log_panel,
            height=10,
            relief=tk.FLAT,
            bg="#111827",
            fg="#D1D5DB",
            insertbackground="#D1D5DB",
            font=("Consolas", 10),
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)
        self.safe_log("请先确认 Cookie 和日期，点击 [拉取场馆状态]。")

    def create_panel(self, parent, title):
        wrapper = tk.Frame(parent, bg=self.colors["panel"], highlightthickness=1, highlightbackground=self.colors["border"])
        title_label = tk.Label(
            wrapper,
            text=title,
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        title_label.pack(anchor=tk.W, padx=14, pady=(10, 0))
        return wrapper

    def make_button(self, parent, text, command, color, width=12, state=tk.NORMAL):
        return tk.Button(
            parent,
            text=text,
            command=command,
            width=width,
            state=state,
            bg=color,
            fg="white",
            activebackground=color,
            activeforeground="white",
            disabledforeground="#9CA3AF",
            relief=tk.FLAT,
            cursor="hand2",
            font=("Microsoft YaHei UI", 10, "bold"),
            padx=10,
            pady=7,
        )

    def safe_log(self, msg):
        def append_text():
            self.log_text.insert(tk.END, msg + "\n")
            self.log_text.see(tk.END)

        self.root.after(0, append_text)

    def auto_fill_cookie(self):
        if self.is_running:
            return

        self.btn_auto_cookie.config(state=tk.DISABLED)
        self.safe_log("正在打开专用浏览器，请在弹出的窗口中登录并进入体育场馆预约页面...")

        t = threading.Thread(target=self.auto_fill_cookie_task)
        t.daemon = True
        t.start()

    def auto_fill_cookie_task(self):
        try:
            browser_name, cookie_text, user_id, user_name, login_account = load_cookie_from_playwright()

            def apply_cookie():
                self.token_var.set(cookie_text)
                self.student_id_var.set(user_id)
                self.student_name_var.set(user_name)
                self.verified_cookie = cookie_text
                self.logged_in_user = (user_id, user_name)
                self.login_account = login_account
                self.btn_auto_cookie.config(state=tk.NORMAL)
                self.safe_log(f"已通过 Playwright/{browser_name} 获取 Cookie，长度：{len(cookie_text)}")
                account_text = f"，统一认证账号：{login_account}" if login_account else ""
                self.safe_log(f"当前登录人：{user_name}（{user_id}）{account_text}")
                messagebox.showinfo(
                    "成功",
                    f"已获取 Cookie 和登录人信息。\n\n"
                    f"场馆系统预约人：{user_name}（{user_id}）"
                    + (f"\n统一认证账号：{login_account}" if login_account else ""),
                )

            self.root.after(0, apply_cookie)
        except Exception as exc:
            error_msg = str(exc)

            def show_error():
                self.btn_auto_cookie.config(state=tk.NORMAL)
                self.safe_log(f"自动获取 Cookie 失败：{error_msg}")
                messagebox.showerror("自动获取失败", error_msg)

            self.root.after(0, show_error)

    def open_payment_page(self, cookie_header):
        self.safe_log("正在打开我的预约页面，请稍后在浏览器中完成支付...")
        t = threading.Thread(target=self.open_payment_page_task, args=(cookie_header,))
        t.daemon = True
        t.start()

    def open_payment_page_task(self, cookie_header):
        try:
            def opened(browser_name):
                self.safe_log(f"已通过 Playwright/{browser_name} 打开我的预约页面，请手动确认并支付。")

            open_my_booking_page(cookie_header, opened_callback=opened)
            self.safe_log("我的预约页面已关闭。")
        except Exception as exc:
            self.safe_log(f"打开我的预约页面失败：{exc}")

    def fetch_slots(self):
        if self.is_running:
            return

        target_date = self.date_var.get().strip()
        current_token = self.token_var.get().strip()

        if not current_token:
            messagebox.showwarning("提示", "请先输入 Cookie/Token！")
            return

        for widget in self.btn_frame.winfo_children():
            widget.destroy()

        payload = {"XQ": "1", "YYRQ": target_date, "YYLX": "2.0", "XMDM": "007"}
        try:
            user_id, user_name = fetch_logged_in_user(current_token)
            self.student_id_var.set(user_id)
            self.student_name_var.set(user_name)
            self.verified_cookie = current_token
            self.logged_in_user = (user_id, user_name)
            self.safe_log(f"已核对当前登录人：{user_name}（{user_id}）")
            request_headers = {**HEADERS, "Cookie": current_token}
            res = requests.post(LIST_URL, headers=request_headers, data=payload, verify=False, timeout=5)
            data_list = res.json()

            if not data_list:
                tk.Label(
                    self.btn_frame,
                    text="当前日期没有返回可展示的时段",
                    bg=self.colors["panel"],
                    fg=self.colors["muted"],
                    font=("Microsoft YaHei UI", 11),
                ).pack(expand=True)
                self.safe_log(f"日期 {target_date} 暂无可展示时段。")
                return

            row, col = 0, 0
            for item in data_list:
                time_code = item.get("CODE")
                status_text = item.get("text")
                btn_text = f"{time_code}\n{status_text}"

                if status_text == "已过期":
                    btn = tk.Button(
                        self.btn_frame,
                        text=btn_text,
                        width=16,
                        height=2,
                        state=tk.DISABLED,
                        bg=self.colors["disabled"],
                        disabledforeground=self.colors["muted"],
                        relief=tk.FLAT,
                        font=("Microsoft YaHei UI", 10),
                    )
                else:
                    bg_color = self.colors["warning"] if status_text == "已满员" else self.colors["primary"]
                    btn = tk.Button(
                        self.btn_frame,
                        text=btn_text,
                        width=16,
                        height=2,
                        bg=bg_color,
                        fg="white",
                        activebackground=bg_color,
                        activeforeground="white",
                        relief=tk.FLAT,
                        cursor="hand2",
                        font=("Microsoft YaHei UI", 10, "bold"),
                        command=lambda t=time_code: self.start_thread(t)
                    )
                btn.grid(row=row, column=col, padx=8, pady=8, sticky="nsew")
                col += 1
                if col > 3:
                    col = 0
                    row += 1

            for grid_col in range(4):
                self.btn_frame.grid_columnconfigure(grid_col, weight=1)

            self.safe_log(f"日期 {target_date} 状态刷新成功。")
        except Exception as e:
            messagebox.showerror("错误", f"拉取失败，请检查日期格式或Cookie\n{e}")

    def stop_sniping(self):
        if self.is_running:
            self.stop_event.set()
            self.btn_stop.config(state=tk.DISABLED)

    def start_thread(self, time_slot):
        if self.is_running: return

        target_date = self.date_var.get().strip()
        current_token = self.token_var.get().strip()
        student_id = self.student_id_var.get().strip()
        student_name = self.student_name_var.get().strip()
        if not current_token:
            messagebox.showwarning("提示", "请先输入 Cookie/Token！")
            return
        if (
            not self.logged_in_user
            or self.verified_cookie != current_token
            or self.logged_in_user != (student_id, student_name)
        ):
            messagebox.showwarning(
                "提示",
                "尚未核对当前 Cookie 的登录人，请先点击“拉取场馆状态”！",
            )
            return

        self.is_running = True
        self.stop_event.clear()

        # 锁定UI
        self.date_entry.config(state=tk.DISABLED)
        self.token_entry.config(state=tk.DISABLED)
        self.student_id_entry.config(state=tk.DISABLED)
        self.student_name_entry.config(state=tk.DISABLED)
        self.btn_auto_cookie.config(state=tk.DISABLED)
        self.btn_fetch.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        for child in self.btn_frame.winfo_children():
            child.config(state=tk.DISABLED)

        t = threading.Thread(
            target=self.sniping_task,
            args=(time_slot, target_date, current_token, student_id, student_name),
        )
        t.daemon = True
        t.start()

    def sniping_task(self, time_slot, target_date, current_token, student_id, student_name):
        start_time, end_time = time_slot.split("-")

        payload = {
            "CDWID": "312801690c364d2cb56df744a39f38f1",
            "YYRQ": target_date,
            "KYYSJD": time_slot,
            "BCXZRS": "0", "XQDM": "1", "YYRGH": student_id, "YYRXM": student_name,
            "YYLX": "2.0", "XMDM": "007", "CGDM": "004", "XQWID": "1",
            "YYKS": f"{target_date} {start_time}",
            "YYJS": f"{target_date} {end_time}",
            "CYRS": "1", "PC_OR_PHONE": "phone"
        }

        session = requests.Session()
        session.headers.update({**HEADERS, "Cookie": current_token})
        self.safe_log(f"锁定时段：{target_date} {time_slot}")
        self.safe_log(
            f"身份核对：提交预约人={student_name}（{student_id}），"
            f"Cookie登录人={self.logged_in_user[1]}（{self.logged_in_user[0]}）"
        )

        attempts = 0
        while not self.stop_event.is_set():
            attempts += 1
            try:
                res_book = session.post(POST_URL, data=payload, timeout=5, verify=False)
                try:
                    res_json = res_book.json()
                    code, msg = res_json.get("code"), res_json.get("msg", "未知")
                    if str(code) == "0" or "成功" in msg:
                        dhid = res_json.get("data", {}).get("DHID", "未知")
                        self.safe_log(f"预约成功，订单号：{dhid}")
                        self.open_payment_page(session.headers.get("Cookie", ""))
                        self.root.after(0, lambda: messagebox.showinfo("成功",
                                                                       f"抢到啦！\n日期: {target_date}\n时段: {time_slot}"))
                        break
                    else:
                        self.safe_log(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] #{attempts} -> {msg}")
                        if "预约人学工号不是当前登录人" in msg:
                            identity_details = (
                                f"本次提交预约人：{student_name}（{student_id}）"
                            )
                            try:
                                actual_id, actual_name = fetch_logged_in_user(current_token)
                                identity_details += (
                                    f"\nCookie 登录人：{actual_name}（{actual_id}）"
                                )
                                self.safe_log(
                                    f"再次读取服务器登录人：{actual_name}（{actual_id}）；"
                                    f"本次提交预约人：{student_name}（{student_id}）"
                                )
                            except Exception as identity_exc:
                                identity_details += f"\nCookie 登录人读取失败：{identity_exc}"
                                self.safe_log(f"重新读取登录人失败：{identity_exc}")
                            self.safe_log("身份不匹配，已停止预约，请查看上面的双方身份信息。")
                            self.root.after(
                                0,
                                lambda details=identity_details: messagebox.showerror(
                                    "登录身份不匹配",
                                    "服务器拒绝了当前身份组合。\n\n" + details,
                                ),
                            )
                            break
                except ValueError:
                    self.safe_log("请求被拦截：Cookie 可能失效")
                    break
            except Exception as e:
                self.safe_log(f"网络异常：{e}")

            for _ in range(5):
                if self.stop_event.is_set(): break
                time.sleep(0.5)

        self.is_running = False

        def restore_ui():
            self.date_entry.config(state=tk.NORMAL)
            self.token_entry.config(state=tk.NORMAL)
            self.student_id_entry.config(state="readonly")
            self.student_name_entry.config(state="readonly")
            self.btn_auto_cookie.config(state=tk.NORMAL)
            self.btn_fetch.config(state=tk.NORMAL)
            self.btn_stop.config(state=tk.DISABLED)
            self.fetch_slots()

        self.root.after(0, restore_ui)


if __name__ == "__main__":
    root = tk.Tk()
    app = SniperGUI(root)
    root.mainloop()
