"""Windows GUI entry point. Opening the application never starts a booking."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import msvcrt
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from app_settings import VERSION, build_config, data_directory, load_preferences, save_preferences, task_summary
from booking_rules import now
from booking_runner import Runner

STATES = {
    'READY': '等待设置', 'STARTING': '正在启动浏览器', 'WAITING_LOGIN': '等待学校登录',
    'ARMED': '已就绪，等待扫描开始', 'SCANNING': '正在查找场地', 'SUBMITTING': '正在校验预约',
    'AWAITING_AGREEMENT': '等待确认学校条款', 'CREATING_ORDER': '正在提交同一场次',
    'SCHOOL_MESSAGE': '正在处理学校提示', 'RETRYING_CONFIRMATION': '正在重试确认',
    'CHECKING_ORDER': '正在核对订单', 'CONFIRMED': '已核验免费订单',
    'ORDER_CREATED': '已生成订单，请核对', 'PAYMENT_STARTING': '正在打开付款窗口',
    'PENDING_PAYMENT': '付款窗口已显示，请手动付款', 'PAYMENT_REVIEW': '请核对支付页面',
    'NEEDS_REVIEW': '需要人工核对', 'EXPIRED': '任务已到截止时间',
    'FAILED': '任务已停止，请查看提示', 'STOPPED': '已停止',
}


def configure_logging(directory):
    """Keep task diagnostics independent from third-party root logger settings."""
    logger = logging.getLogger('neu_court_booking')
    logger.setLevel(logging.INFO)
    logger.disabled = False
    logger.propagate = False
    for previous in list(logger.handlers):
        logger.removeHandler(previous)
        previous.close()
    handler = RotatingFileHandler(Path(directory) / 'booking.log', maxBytes=1_000_000,
                                  backupCount=2, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(handler)
    logger.info('Application started: %s', VERSION)
    return logger


class App(tk.Tk):
    def __init__(self, directory):
        super().__init__()
        self.directory = directory
        self.runner = None
        self.events = queue.Queue()
        self.closing = False
        self.title(f'东大羽毛球预约助手 · {VERSION}')
        self.geometry(f'940x{min(880, self.winfo_screenheight() - 90)}')
        self.minsize(850, 700)
        self.configure(padx=22, pady=16)
        self.option_add('*Font', ('Microsoft YaHei UI', 10))
        ttk.Style(self).configure('Title.TLabel', font=('Microsoft YaHei UI', 19, 'bold'))
        ttk.Label(self, text='南湖羽毛球预约助手', style='Title.TLabel').pack(anchor='w')
        ttk.Label(self, text='设定扫描时间与场地 → 本人登录学校 → 自动尝试预约 → 核对订单 / 手动付款',
                  foreground='#536579').pack(anchor='w', pady=(5, 14))
        try:
            config = load_preferences(directory)
        except (ValueError, OSError) as error:
            from app_settings import default_config
            config = default_config()
            self.after(100, lambda msg=str(error): messagebox.showwarning('本地设置', msg, parent=self))
        self.fields = {}
        settings_area = ttk.Frame(self)
        settings_area.pack(fill='both', expand=True, pady=(0, 10))
        self.settings_canvas = tk.Canvas(settings_area, highlightthickness=0, borderwidth=0)
        settings_scroll = ttk.Scrollbar(settings_area, orient='vertical', command=self.settings_canvas.yview)
        self.settings_canvas.configure(yscrollcommand=settings_scroll.set)
        settings_scroll.pack(side='right', fill='y')
        self.settings_canvas.pack(side='left', fill='both', expand=True)
        self.settings = ttk.Frame(self.settings_canvas)
        settings_window = self.settings_canvas.create_window((0, 0), window=self.settings, anchor='nw')
        self.settings.bind('<Configure>', lambda _: self.settings_canvas.configure(
            scrollregion=self.settings_canvas.bbox('all')))
        self.settings_canvas.bind('<Configure>', lambda event: self.settings_canvas.itemconfigure(
            settings_window, width=event.width))
        self.bind_all('<MouseWheel>', self.scroll_settings, add='+')
        task = ttk.LabelFrame(self.settings, text='1  什么时候扫描（北京时间）', padding=12)
        task.pack(fill='x', pady=(0, 10))
        self.entry(task, '预约日期', 'target_date', config['target_date'], 0, 0, 14)
        self.entry(task, '扫描开始', 'scan_start', config['scan_start'], 0, 2, 9)
        self.entry(task, '扫描结束', 'scan_end', config['scan_end'], 0, 4, 9)
        ttk.Label(task, text='时间填 HH:MM；根据学校当天规则自行设置。仅在这段时间内查询和提交，不会延续到第二天。',
                  foreground='#536579').grid(row=1, column=0, columnspan=6, sticky='w', pady=(8, 0))
        self.entry(task, '查询间隔（秒）', 'poll_seconds', f"{config['poll_seconds']:g}", 2, 0, 9)
        ttk.Label(task, text='推荐0.5秒；可填0.5–60秒。查询依次执行，网站繁忙时自动放慢。',
                  foreground='#536579').grid(row=2, column=2, columnspan=4, sticky='w')
        slots = ttk.LabelFrame(self.settings, text='2  想打哪个时段、哪些场地', padding=12)
        slots.pack(fill='x', pady=(0, 10))
        self.entry(slots, '打球最早开始', 'play_start', config['play_start'], 0, 0, 9)
        self.entry(slots, '打球最晚结束', 'play_end', config['play_end'], 0, 2, 9)
        ttk.Label(slots, text='例如14:00–21:00：只选完整落在这个范围内的场次，免费和付费一起考虑。',
                  foreground='#536579').grid(row=1, column=0, columnspan=4, sticky='w', pady=(3, 8))
        self.entry(slots, '场地编号（可选）', 'court_numbers', ','.join(map(str, config['court_numbers'])), 2, 0, 28)
        ttk.Label(slots, text='留空＝全部场地；填10,8,12＝只选这三片，按填写顺序优先尝试，任意一场成功就停止。',
                  foreground='#245a96', wraplength=820).grid(row=3, column=0, columnspan=4, sticky='w', pady=(3, 5))
        ttk.Label(slots, text='以学校实际可用场次为准；付费每场最多30元，程序只打开付款窗口，由你手动付款。',
                  foreground='#536579', wraplength=820).grid(row=4, column=0, columnspan=4, sticky='w', pady=(3, 0))
        people = ttk.LabelFrame(self.settings, text='3  同行人（不含本人，填写1–5位）', padding=12)
        people.pack(fill='x', pady=(0, 10))
        ttk.Label(people, text='姓名').grid(row=0, column=1, sticky='w')
        ttk.Label(people, text='统一认证账号 / 学号 / 工号').grid(row=0, column=2, sticky='w')
        self.people = []
        for index in range(5):
            ttk.Label(people, text=str(index+1)).grid(row=index+1, column=0, padx=(0, 10))
            saved = config['companions'][index] if index < len(config['companions']) else {}
            name, number = tk.StringVar(value=saved.get('name', '')), tk.StringVar(value=saved.get('id', ''))
            ttk.Entry(people, textvariable=name, width=19).grid(row=index+1, column=1, padx=(0, 16), pady=2)
            ttk.Entry(people, textvariable=number, width=29).grid(row=index+1, column=2, sticky='w', pady=2)
            self.people.append((name, number))
        ttk.Label(people, text='只保存在本机。\n请勿把使用后的个人数据发给别人。',
                  foreground='#536579').grid(row=1, column=3, rowspan=4, padx=20, sticky='w')
        self.auto_terms = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.settings, text='登录后阅读当前学校条款，并选择是否授权程序自动勾选',
                        variable=self.auto_terms).pack(anchor='w', pady=(0, 6))
        ttk.Label(self.settings, text='关闭此选项时，预约确认页的条款由本人手动勾选。',
                  foreground='#536579').pack(anchor='w', pady=(0, 10))
        controls = ttk.Frame(self)
        controls.pack(fill='x', pady=(0, 10))
        self.start_button = ttk.Button(controls, text='确认设置并开始', command=self.start)
        self.start_button.pack(side='left')
        self.stop_button = ttk.Button(controls, text='停止任务', command=self.stop_task, state='disabled')
        self.stop_button.pack(side='left', padx=10)
        ttk.Button(controls, text='使用说明', command=self.show_help).pack(side='right')
        ttk.Button(controls, text='打开本机数据目录', command=lambda: os.startfile(self.directory)).pack(side='right', padx=10)
        self.status = ttk.Label(self, text='等待设置；程序尚未启动预约。', foreground='#245b8f')
        self.status.pack(anchor='w', pady=(0, 5))
        self.clock = ttk.Label(self, text='', foreground='#536579')
        self.clock.pack(anchor='w', pady=(0, 6))
        log_frame = ttk.Frame(self)
        log_frame.pack(fill='x')
        self.log = tk.Text(log_frame, height=4, wrap='word', state='disabled', relief='flat',
                           background='#f4f7fb', padx=10, pady=8, font=('Microsoft YaHei UI', 9))
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.tick)

    def entry(self, parent, label, key, value, row, column, width):
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky='w', padx=(0, 6), pady=4)
        var = tk.StringVar(value=value)
        widget = ttk.Entry(parent, textvariable=var, width=width)
        widget.grid(row=row, column=column+1, sticky='w', padx=(0, 15), pady=4)
        self.fields[key] = (var, widget)

    def scroll_settings(self, event):
        widget = event.widget
        while widget is not None:
            if widget is self.settings or widget is self.settings_canvas:
                if self.settings.winfo_height() > self.settings_canvas.winfo_height():
                    self.settings_canvas.yview_scroll(-int(event.delta / 120), 'units')
                    return 'break'
                return
            widget = widget.master

    def publish(self, message): self.events.put(('log', message))

    def approve_terms(self, text, stop):
        answer = {'approved': False, 'ready': threading.Event()}
        self.events.put(('terms', (text, answer)))
        while not stop.is_set() and not answer['ready'].wait(0.2):
            pass
        return answer['approved'] and not stop.is_set()

    def terms_dialog(self, text, answer):
        if self.closing or not self.runner or self.runner.stop.is_set():
            answer['ready'].set()
            return
        dialog = tk.Toplevel(self)
        dialog.title('请阅读当前学校条款')
        dialog.geometry('720x570')
        dialog.transient(self)
        ttk.Label(dialog, text='授权只用于本次任务；条款内容变化时程序会暂停。', padding=12).pack(anchor='w')
        content = tk.Text(dialog, wrap='word', padx=12, pady=10, font=('Microsoft YaHei UI', 10))
        content.insert('1.0', text)
        content.configure(state='disabled')
        scrollbar = ttk.Scrollbar(dialog, command=content.yview)
        content.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        content.pack(fill='both', expand=True, padx=12)
        buttons = ttk.Frame(dialog, padding=12)
        buttons.pack(fill='x')

        def finish(approved):
            answer['approved'] = approved
            answer['ready'].set()
            dialog.destroy()

        ttk.Button(buttons, text='已阅读，同意本次自动勾选', command=lambda: finish(True)).pack(side='left')
        ttk.Button(buttons, text='本次手动勾选', command=lambda: finish(False)).pack(side='right')
        dialog.protocol('WM_DELETE_WINDOW', lambda: finish(False))
        dialog.grab_set()

    def start(self):
        if self.runner and self.runner.thread.is_alive():
            return
        try:
            values = {key: var.get() for key, (var, _) in self.fields.items()}
            values.update(auto_accept_terms=self.auto_terms.get(),
                          companions=[{'name': name.get(), 'id': number.get()} for name, number in self.people])
            config = build_config(values)
            summary = task_summary(config)
            if not messagebox.askokcancel('核对本次预约任务', summary + '\n\n确认后打开学校浏览器。', parent=self):
                return
            save_preferences(config, self.directory)
            self.runner = Runner(config, self.directory / 'runtime', self.publish, self.approve_terms)
            self.start_button.configure(state='disabled')
            self.stop_button.configure(state='normal')
            for child in self.settings.winfo_children():
                self.set_editable(child, False)
            self.publish(summary)
            self.runner.thread.start()
        except (ValueError, OSError, TypeError) as error:
            messagebox.showerror('请检查设置', str(error), parent=self)

    def set_editable(self, widget, enabled):
        if isinstance(widget, (ttk.Entry, ttk.Checkbutton, ttk.Radiobutton, ttk.Button)):
            widget.configure(state='normal' if enabled else 'disabled')
        for child in widget.winfo_children():
            self.set_editable(child, enabled)

    def stop_task(self):
        if self.runner:
            self.runner.stop.set()
            self.publish('已请求停止。当前页面操作结束后关闭由助手启动的浏览器。')

    def show_help(self):
        root = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
        doc = root / 'docs' / '使用说明.md'
        if doc.exists():
            os.startfile(doc)
        else:
            messagebox.showinfo('使用步骤', '填写扫描起止时间、打球时段、场地和同行人，点击“确认设置并开始”，然后本人登录学校。\n'
                                '保持电脑开机联网、不休眠。最多预约一场；付费由本人完成。', parent=self)

    def close(self):
        self.closing = True
        self.stop_task()
        self.finish_close()

    def finish_close(self):
        if self.runner and self.runner.thread.is_alive():
            self.after(200, self.finish_close)
        else:
            self.destroy()

    def tick(self):
        self.clock.configure(text='北京时间  ' + now().strftime('%Y-%m-%d %H:%M:%S'))
        if self.runner:
            self.status.configure(text=STATES.get(self.runner.state, '正在运行'))
            if not self.runner.thread.is_alive() and self.runner.thread.ident is not None:
                self.start_button.configure(state='normal')
                self.stop_button.configure(state='disabled')
                for child in self.settings.winfo_children():
                    self.set_editable(child, True)
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'terms':
                self.terms_dialog(*value)
            else:
                self.log.configure(state='normal')
                self.log.insert('end', now().strftime('%H:%M:%S') + '  ' + value + '\n')
                self.log.see('end')
                self.log.configure(state='disabled')
        self.after(200, self.tick)


def self_test(output):
    """Packaged-runtime smoke test: local GUI + about:blank, no school requests."""
    from selenium import webdriver
    from selenium.webdriver.common.selenium_manager import SeleniumManager
    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    root.destroy()
    binary = SeleniumManager._get_binary()
    manager = subprocess.run([str(binary), '--version'], capture_output=True, text=True,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=15)
    if manager.returncode:
        raise RuntimeError('Bundled Selenium Manager did not run')
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    os.environ['SE_AVOID_STATS'] = 'true'
    with webdriver.Chrome(options=options) as driver:
        driver.get('about:blank')
        if driver.current_url != 'about:blank':
            raise RuntimeError('Chrome smoke test failed')
    Path(output).write_text(json.dumps({'ok': True, 'version': VERSION, 'gui': True, 'chrome': True,
                                       'selenium_manager': manager.stdout.strip()}, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test', metavar='RESULT_JSON')
    parser.add_argument('--ui-smoke-test', metavar='RESULT_JSON')
    args = parser.parse_args()
    if args.self_test:
        try:
            self_test(args.self_test)
        except Exception as error:
            Path(args.self_test).write_text(json.dumps({'ok': False, 'error': type(error).__name__,
                                                       'detail': str(error)}), encoding='utf-8')
            raise SystemExit(1)
        return
    directory = data_directory()
    configure_logging(directory)
    with open(directory / 'instance.lock', 'a+b') as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b'1')
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo('预约助手', '预约助手已打开，请使用已有窗口。')
            root.destroy()
            return
        app = App(directory)
        if args.ui_smoke_test:
            def finish_smoke():
                if app.runner is not None:
                    raise RuntimeError('GUI must not start a booking automatically')
                Path(args.ui_smoke_test).write_text(json.dumps({'ok': True, 'no_auto_start': True}), encoding='utf-8')
                app.destroy()
            app.after(1500, finish_smoke)
        app.mainloop()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Keep browser URLs, signatures and exception payloads out of startup diagnostics.
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror('预约助手启动失败', f'发生 {type(error).__name__}。请检查Chrome、网络和本机数据目录。')
        root.destroy()
        raise SystemExit(1)
