import requests
import time
import datetime
import urllib3
import tkinter as tk
from tkinter import messagebox
import threading

# 禁用 HTTPS 证书警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= 核心配置区 =================
# 建议在 README 中提示用户，只需在这里填写一次 Cookie
YOUR_LATEST_COOKIE = "_WEU=; insert_cookie=; route=; MOD_AUTH_CAS=; JSESSIONID="

# API 接口地址
LIST_URL = "https://ehall.szu.edu.cn/qljfwapp/sys/lwSzuCgyy/sportVenue/getTimeList.do"
POST_URL = "https://ehall.szu.edu.cn/qljfwapp/sys/lwSzuCgyy/sportVenue/insertVenueBookingInfo.do"

# 基础请求头
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://ehall.szu.edu.cn",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.6668.101 Safari/537.36",
    "Cookie": YOUR_LATEST_COOKIE
}

class SniperGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("深大体育馆自动捡漏器 v2.1")
        self.root.geometry("620x600")

        self.stop_event = threading.Event()
        self.is_running = False

        # --- 顶部：日期选择区 ---
        frame_date = tk.Frame(self.root)
        frame_date.pack(pady=10)

        tk.Label(frame_date, text="预约日期:", font=("Arial", 11)).pack(side=tk.LEFT, padx=5)

        # 默认显示今日日期
        self.date_var = tk.StringVar(value=datetime.date.today().strftime("%Y-%m-%d"))
        self.date_entry = tk.Entry(frame_date, textvariable=self.date_var, width=12, font=("Arial", 11))
        self.date_entry.pack(side=tk.LEFT, padx=5)

        tk.Label(frame_date, text="(格式: YYYY-MM-DD)", fg="gray").pack(side=tk.LEFT)

        # --- 操作按钮区 ---
        frame_ops = tk.Frame(self.root)
        frame_ops.pack(pady=5)

        self.btn_fetch = tk.Button(frame_ops, text="🔍 拉取场馆状态", command=self.fetch_slots, bg="#4CAF50", fg="white", width=15)
        self.btn_fetch.pack(side=tk.LEFT, padx=10)

        self.btn_stop = tk.Button(frame_ops, text="⏹ 停止当前捡漏", command=self.stop_sniping, bg="#F44336", fg="white", state=tk.DISABLED, width=15)
        self.btn_stop.pack(side=tk.LEFT, padx=10)

        # --- 按钮容器 ---
        self.btn_frame = tk.Frame(self.root)
        self.btn_frame.pack(pady=10)

        # --- 日志输出区 ---
        self.log_text = tk.Text(self.root, height=18, width=75)
        self.log_text.pack(pady=10)
        self.safe_log("💡 请先确认日期，点击 [拉取场馆状态]...")

    def safe_log(self, msg):
        def append_text():
            self.log_text.insert(tk.END, msg + "\n")
            self.log_text.see(tk.END)
        self.root.after(0, append_text)

    def fetch_slots(self):
        if self.is_running:
            return

        # 每次刷新前，先从输入框获取最新日期
        target_date = self.date_var.get().strip()

        for widget in self.btn_frame.winfo_children():
            widget.destroy()

        payload = {"XQ": "1", "YYRQ": target_date, "YYLX": "2.0", "XMDM": "007"}
        try:
            # 更新全局 Header 中的 Cookie (以防用户中途修改代码配置)
            HEADERS["Cookie"] = YOUR_LATEST_COOKIE
            res = requests.post(LIST_URL, headers=HEADERS, data=payload, verify=False, timeout=5)
            data_list = res.json()

            row, col = 0, 0
            for item in data_list:
                time_code = item.get("CODE")
                status_text = item.get("text")
                btn_text = f"{time_code}\n[{status_text}]"

                if status_text == "已过期":
                    btn = tk.Button(self.btn_frame, text=btn_text, width=15, height=2, state=tk.DISABLED)
                else:
                    bg_color = "#FF9800" if status_text == "已满员" else "#2196F3"
                    btn = tk.Button(self.btn_frame, text=btn_text, width=15, height=2, bg=bg_color, fg="white",
                                    command=lambda t=time_code: self.start_thread(t))
                btn.grid(row=row, column=col, padx=5, pady=5)
                col += 1
                if col > 3: col = 0; row += 1

            self.safe_log(f"✅ 日期 {target_date} 状态刷新成功！")
        except Exception as e:
            messagebox.showerror("错误", f"拉取失败，请检查日期格式或Cookie\n{e}")

    def stop_sniping(self):
        if self.is_running:
            self.stop_event.set()
            self.btn_stop.config(state=tk.DISABLED)

    def start_thread(self, time_slot):
        if self.is_running: return
        self.is_running = True
        self.stop_event.clear()

        # 锁定UI
        self.date_entry.config(state=tk.DISABLED)
        self.btn_fetch.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        for child in self.btn_frame.winfo_children():
            child.config(state=tk.DISABLED)

        t = threading.Thread(target=self.sniping_task, args=(time_slot,))
        t.daemon = True
        t.start()

    def sniping_task(self, time_slot):
        target_date = self.date_var.get().strip()
        start_time, end_time = time_slot.split("-")

        payload = {
            "CDWID": "312801690c364d2cb56df744a39f38f1",
            "YYRQ": target_date,
            "KYYSJD": time_slot,
            "BCXZRS": "0", "XQDM": "1", "YYRGH": "2510103005", "YYRXM": "胡嘉俊",
            "YYLX": "2.0", "XMDM": "007", "CGDM": "004", "XQWID": "1",
            "YYKS": f"{target_date} {start_time}",
            "YYJS": f"{target_date} {end_time}",
            "CYRS": "1", "PC_OR_PHONE": "phone"
        }

        session = requests.Session()
        session.headers.update(HEADERS)
        self.safe_log(f"🚀 锁定: {target_date} {time_slot}")

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
                        self.safe_log(f"🎉 成功！订单号: {dhid}")
                        self.root.after(0, lambda: messagebox.showinfo("成功", f"抢到啦！\n日期: {target_date}\n时段: {time_slot}"))
                        break
                    else:
                        self.safe_log(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] #{attempts} -> {msg}")
                except ValueError:
                    self.safe_log("❌ 拦截: Cookie可能失效")
                    break
            except Exception as e:
                self.safe_log(f"❌ 网络异常: {e}")

            for _ in range(5):
                if self.stop_event.is_set(): break
                time.sleep(0.5)

        self.is_running = False
        def restore_ui():
            self.date_entry.config(state=tk.NORMAL)
            self.btn_fetch.config(state=tk.NORMAL)
            self.btn_stop.config(state=tk.DISABLED)
            self.fetch_slots()
        self.root.after(0, restore_ui)

if __name__ == "__main__":
    root = tk.Tk()
    app = SniperGUI(root)
    root.mainloop()