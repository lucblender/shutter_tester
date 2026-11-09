"""Shutter tester - control app (modern UI).

Requires: pip install customtkinter pyserial
Protocol: see PROTOCOL.md
"""
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
import serial
import serial.tools.list_ports

BAUD = 115200
SPEEDS = {"1/500": 500, "1/1000": 1000, "1/2000": 2000}
SPEEDS_REV = {v: k for k, v in SPEEDS.items()}
EXPECTED = {f"1/{n}": n for n in (1, 2, 4, 8, 15, 30, 60, 125, 250, 500, 1000)}
EXPECTED_REV = {v: k for k, v in EXPECTED.items()}
MODE_LABELS = {"T": "Trigger", "L": "Lux meter"}
MODE_CODES = {v: k for k, v in MODE_LABELS.items()}
HISTORY = 10
PORT_CHECK_S = 2.0          # how often the reader checks the port still exists
HELLO_TIMEOUT_S = 3.0       # no answer to HELLO after this -> warning

# (light, dark) colour pairs
BG = ("#f1f5f9", "#0b1220")
CARD = ("#ffffff", "#162032")
TEXT = ("#0f172a", "#f1f5f9")
MUTED = ("#64748b", "#94a3b8")
GRID = ("#e2e8f0", "#2a3a52")
CURVE = ("#2563eb", "#60a5fa")
AXIS = ("#94a3b8", "#475569")
ACCENT = "#3b82f6"
RED = "#ef4444"

STATES = {
    "disconnected": ("Disconnected", "#94a3b8"),
    "connecting": ("Connecting…", "#f59e0b"),
    "connected": ("Connected", "#22c55e"),
    "noresp": ("No response", "#f59e0b"),
    "lost": ("Connection lost", RED),
}


def col(pair):
    """Pick the colour for the current light/dark appearance."""
    return pair[1] if ctk.get_appearance_mode() == "Dark" else pair[0]


def format_shutter(us: int) -> str:
    """Duration in microseconds -> '1/250'."""
    if us <= 0:
        return "-"
    if us >= 1_000_000:
        return f"{us / 1_000_000:.2f} s"
    return f"1/{round(1_000_000 / us)}"


class App:
    def __init__(self, root: ctk.CTk):
        self.root = root
        root.title("Shutter Tester")
        root.geometry("600x940")
        root.minsize(540, 800)
        root.configure(fg_color=BG)

        self.ser = None
        self.running = False
        self.session = 0            # incremented on each connect/disconnect
        self.state = "disconnected"
        self.hello_ok = False
        self.q = queue.Queue()

        self.mode = "T"
        self.applied_threshold = 100
        self.thr_var = tk.StringVar(value="100")
        self.port_var = tk.StringVar()
        self.curve_var = tk.IntVar(value=0)

        self.entries = []           # history: dicts {stamp, speed, us, curve}
        self.history_rows = []      # widgets currently shown in the history list
        self.selected = None
        self.current_curve = None
        self._appearance = ctk.get_appearance_mode()

        self.build_ui()
        self.refresh_ports()
        self.set_state("disconnected")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(50, self.poll_queue)

    # ------------------------------------------------------------- UI
    def card(self, parent, **kw):
        return ctk.CTkFrame(parent, fg_color=CARD, corner_radius=16, **kw)

    def build_ui(self):
        pad = {"padx": 16}

        # header
        header = ctk.CTkFrame(self.root, fg_color="transparent")
        header.pack(fill="x", pady=(14, 6), **pad)
        ctk.CTkLabel(header, text="Shutter Tester", text_color=TEXT,
                     font=ctk.CTkFont(size=22, weight="bold")).pack(side="left")
        self.pill = ctk.CTkFrame(header, fg_color=CARD, corner_radius=14)
        self.pill.pack(side="right")
        self.pill_dot = ctk.CTkLabel(
            self.pill, text="●", width=12, font=ctk.CTkFont(size=14))
        self.pill_dot.pack(side="left", padx=(12, 2), pady=4)
        self.pill_text = ctk.CTkLabel(self.pill, text="", text_color=TEXT,
                                      font=ctk.CTkFont(size=13))
        self.pill_text.pack(side="left", padx=(0, 12), pady=4)

        # connection card
        conn = self.card(self.root)
        conn.pack(fill="x", pady=6, **pad)
        inner = ctk.CTkFrame(conn, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=12)
        self.port_box = ctk.CTkComboBox(inner, variable=self.port_var, values=[""],
                                        state="readonly", width=240)
        self.port_box.pack(side="left")
        self.refresh_btn = ctk.CTkButton(inner, text="↻", width=36, fg_color=GRID,
                                         text_color=TEXT, hover_color=AXIS,
                                         command=self.refresh_ports)
        self.refresh_btn.pack(side="left", padx=8)
        self.connect_btn = ctk.CTkButton(inner, text="Connect", width=120,
                                         command=self.toggle_connection)
        self.connect_btn.pack(side="right")

        # settings card
        sc = self.card(self.root)
        sc.pack(fill="x", pady=6, **pad)
        grid = ctk.CTkFrame(sc, fg_color="transparent")
        grid.pack(fill="x", padx=14, pady=12)
        grid.columnconfigure(1, weight=1)

        def label(text, r):
            ctk.CTkLabel(grid, text=text, text_color=MUTED, anchor="w", width=110
                         ).grid(row=r, column=0, sticky="w", pady=6)

        label("Mode", 0)
        self.mode_seg = ctk.CTkSegmentedButton(grid, values=list(MODE_CODES),
                                               command=self.on_mode)
        self.mode_seg.set(MODE_LABELS["T"])
        self.mode_seg.grid(row=0, column=1, sticky="w")

        label("Trigger value", 1)
        thr_row = ctk.CTkFrame(grid, fg_color="transparent")
        thr_row.grid(row=1, column=1, sticky="w")
        self.thr_entry = ctk.CTkEntry(
            thr_row, textvariable=self.thr_var, width=80)
        self.thr_entry.pack(side="left")
        self.thr_entry.bind("<Return>", lambda e: self.on_threshold())
        self.thr_btn = ctk.CTkButton(
            thr_row, text="Set", width=60, command=self.on_threshold)
        self.thr_btn.pack(side="left", padx=8)

        label("Fastest speed", 2)
        self.speed_seg = ctk.CTkSegmentedButton(grid, values=list(SPEEDS),
                                                command=self.on_min_speed)
        self.speed_seg.set("1/1000")
        self.speed_seg.grid(row=2, column=1, sticky="w")

        label("Store curve", 3)
        curve_row = ctk.CTkFrame(grid, fg_color="transparent")
        curve_row.grid(row=3, column=1, sticky="w")
        self.curve_switch = ctk.CTkSwitch(curve_row, text="", variable=self.curve_var,
                                          width=46, command=self.on_curve_toggle)
        self.curve_switch.pack(side="left")
        ctk.CTkLabel(curve_row, text="Expected", text_color=MUTED).pack(
            side="left", padx=(8, 6))
        self.expected_menu = ctk.CTkOptionMenu(curve_row, values=list(EXPECTED), width=90,
                                               command=self.on_expected)
        self.expected_menu.set("1/125")
        self.expected_menu.pack(side="left")

        self.settings_widgets = [self.mode_seg, self.thr_entry, self.thr_btn,
                                 self.speed_seg, self.curve_switch]

        # footer (packed before the content so it stays at the bottom)
        self.footer = ctk.CTkLabel(self.root, text="", text_color=MUTED, anchor="w",
                                   font=ctk.CTkFont(size=12))
        self.footer.pack(side="bottom", fill="x", padx=18, pady=(4, 10))

        # content area
        self.content = ctk.CTkFrame(self.root, fg_color="transparent")
        self.content.pack(fill="both", expand=True, pady=6, **pad)

        # trigger view
        self.trigger_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        result = self.card(self.trigger_frame)
        result.pack(fill="x", pady=(0, 8))
        self.big_shutter = ctk.CTkLabel(result, text="-", text_color=TEXT,
                                        font=ctk.CTkFont(size=84, weight="bold"))
        self.big_shutter.pack(pady=(14, 0))
        self.big_detail = ctk.CTkLabel(result, text="waiting for shutter…", text_color=MUTED,
                                       font=ctk.CTkFont(size=14))
        self.big_detail.pack(pady=(0, 14))

        self.plot_card = self.card(self.trigger_frame)
        self.plot = tk.Canvas(self.plot_card, height=230,
                              highlightthickness=0, bd=0)
        self.plot.pack(fill="both", expand=True, padx=10, pady=10)
        self.plot.bind("<Configure>", lambda e: self.draw_curve())

        self.history_card = self.card(self.trigger_frame)
        self.history_card.pack(fill="both", expand=True)
        ctk.CTkLabel(self.history_card, text="Last shutters", text_color=MUTED, anchor="w",
                     font=ctk.CTkFont(size=12)).pack(fill="x", padx=14, pady=(10, 0))
        self.history_box = ctk.CTkScrollableFrame(self.history_card, fg_color="transparent",
                                                  height=150)
        self.history_box.pack(fill="both", expand=True,
                              padx=(8, 4), pady=(2, 8))

        # lux view
        self.lux_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        lux_card = self.card(self.lux_frame)
        lux_card.pack(fill="x")
        self.big_lux = ctk.CTkLabel(lux_card, text="-", text_color=TEXT,
                                    font=ctk.CTkFont(size=110, weight="bold"))
        self.big_lux.pack(pady=(30, 4))
        ctk.CTkLabel(lux_card, text="raw analog value (0-1023)", text_color=MUTED
                     ).pack()
        self.lux_bar = ctk.CTkProgressBar(lux_card, height=14)
        self.lux_bar.set(0)
        self.lux_bar.pack(fill="x", padx=24, pady=(14, 30))

        self.show_mode_view()
        self.update_curve_ui()
        self.render_history()

    def show_mode_view(self):
        self.trigger_frame.pack_forget()
        self.lux_frame.pack_forget()
        frame = self.trigger_frame if self.mode == "T" else self.lux_frame
        frame.pack(fill="both", expand=True)

    def update_curve_ui(self):
        on = bool(self.curve_var.get())
        if on:
            self.plot_card.pack(fill="x", pady=(
                0, 8), before=self.history_card)
        else:
            self.plot_card.pack_forget()
        enabled = on and self.state == "connected"
        self.expected_menu.configure(state="normal" if enabled else "disabled")

    # ----------------------------------------------------------- state
    def set_state(self, state: str, message: str = ""):
        self.state = state
        text, color = STATES[state]
        self.pill_dot.configure(text_color=color)
        self.pill_text.configure(text=text)
        if message:
            self.footer.configure(
                text=message, text_color=RED if state == "lost" else MUTED)

        connected = state == "connected"
        for w in self.settings_widgets:
            w.configure(state="normal" if connected else "disabled")
        self.update_curve_ui()

        has_port = self.ser is not None
        self.port_box.configure(state="disabled" if has_port else "readonly")
        self.refresh_btn.configure(state="disabled" if has_port else "normal")
        if has_port:
            self.connect_btn.configure(text="Disconnect", fg_color=GRID, text_color=TEXT,
                                       hover_color=AXIS)
        else:
            label = "Reconnect" if state == "lost" else "Connect"
            self.connect_btn.configure(text=label, fg_color=ACCENT, text_color="white",
                                       hover_color="#2563eb")

    def say(self, text: str, error: bool = False):
        self.footer.configure(text=text, text_color=RED if error else MUTED)

    # ------------------------------------------------------ connection
    def refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_box.configure(values=ports or [""])
        if ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])
        elif not ports:
            self.port_var.set("")

    def toggle_connection(self):
        if self.ser:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        if not self.port_var.get():
            messagebox.showwarning("Port", "Select a serial port first.")
            return
        try:
            ser = serial.Serial(self.port_var.get(), BAUD, timeout=0.1)
        except serial.SerialException as e:
            messagebox.showerror("Connection failed", str(e))
            return
        self.ser = ser
        self.running = True
        self.session += 1
        self.hello_ok = False
        threading.Thread(target=self.reader, args=(
            ser, self.session), daemon=True).start()
        self.set_state("connecting", "Connecting… (the board may reset)")
        # many boards reset when the port opens: wait before talking
        self.root.after(2000, lambda s=self.session: self.handshake(s))

    def handshake(self, session):
        if session != self.session or not self.ser:
            return
        self.send("HELLO")
        self.send("GET")  # retrieve current settings
        self.root.after(int(HELLO_TIMEOUT_S * 1000),
                        lambda: self.hello_check(session))

    def hello_check(self, session):
        if session == self.session and self.ser and not self.hello_ok:
            self.set_state(
                "noresp", "No answer from the device: wrong port or wrong firmware?")

    def teardown(self):
        """Close the port and invalidate the reader thread."""
        self.running = False
        self.session += 1
        ser, self.ser = self.ser, None
        if ser:
            try:
                ser.close()
            except (serial.SerialException, OSError):
                pass

    def disconnect(self):
        self.teardown()
        self.set_state("disconnected", "Disconnected")

    def connection_lost(self):
        if not self.ser:
            return
        self.teardown()
        self.refresh_ports()
        self.set_state(
            "lost", "Device disconnected. Plug it back in and press Reconnect.")

    def on_close(self):
        self.teardown()
        self.root.destroy()

    # -------------------------------------------------------- serial IO
    def reader(self, ser, session):
        last_check = time.monotonic()
        while self.running and session == self.session:
            try:
                line = ser.readline().decode(errors="ignore").strip()
            except (serial.SerialException, OSError, TypeError, ValueError):
                self.q.put((session, "__LOST__"))
                return
            if line:
                self.q.put((session, line))
            # the read may keep timing out quietly after unplug on some systems,
            # so also check the port still exists
            if time.monotonic() - last_check > PORT_CHECK_S:
                last_check = time.monotonic()
                present = [
                    p.device for p in serial.tools.list_ports.comports()]
                if ser.port not in present:
                    self.q.put((session, "__LOST__"))
                    return

    def send(self, text: str):
        if not self.ser:
            return
        try:
            self.ser.write((text + "\n").encode())
        except (serial.SerialException, OSError):
            self.connection_lost()

    def poll_queue(self):
        try:
            while True:
                session, line = self.q.get_nowait()
                if session != self.session:
                    continue          # stale message from an old connection
                if line == "__LOST__":
                    self.connection_lost()
                else:
                    self.handle_line(line)
        except queue.Empty:
            pass
        # follow system light/dark changes for the plot
        if ctk.get_appearance_mode() != self._appearance:
            self._appearance = ctk.get_appearance_mode()
            self.draw_curve()
        self.root.after(50, self.poll_queue)

    # --------------------------------------------------------- protocol
    def handle_line(self, line: str):
        parts = line.split()
        if not parts:
            return
        cmd, args = parts[0], parts[1:]
        try:
            if cmd == "SHUT":
                self.on_shutter(int(args[0]))
            elif cmd == "IGN":
                self.say(f"Ignored too-fast event ({int(args[0])} µs)")
            elif cmd == "CURVE":
                self.on_curve(int(args[0]), int(args[1]), args[2:])
            elif cmd == "LUX":
                self.big_lux.configure(text=args[0])
                self.lux_bar.set(min(int(args[0]), 1023) / 1023)
            elif cmd == "CFG":
                self.on_config(args[0], int(args[1]), int(args[2]),
                               bool(int(args[3])), int(args[4]))
            elif cmd == "HELLO":
                self.hello_ok = True
                self.set_state("connected", f"Device: {' '.join(args)}")
            elif cmd == "OK":
                self.say("OK")
            elif cmd == "ERR":
                self.say("Error: " + " ".join(args), error=True)
        except (IndexError, ValueError):
            self.say(f"Bad message: {line[:60]}", error=True)

    def on_config(self, mode, thr, min_speed, curve, expected):
        self.mode = mode
        self.mode_seg.set(MODE_LABELS.get(mode, "Trigger"))
        self.thr_var.set(str(thr))
        self.applied_threshold = thr
        self.speed_seg.set(SPEEDS_REV.get(min_speed, "1/1000"))
        if curve:
            self.curve_switch.select()
        else:
            self.curve_switch.deselect()
        self.expected_menu.set(EXPECTED_REV.get(expected, "1/125"))
        self.update_curve_ui()
        self.show_mode_view()
        self.say("Settings loaded from device")

    def on_shutter(self, us: int):
        self.big_shutter.configure(text=format_shutter(us))
        self.big_detail.configure(text=f"{us / 1000:.2f} ms  ({us} µs)")
        self.entries.insert(0, {
            "stamp": time.strftime("%H:%M:%S"),
            "speed": format_shutter(us),
            "us": us,
            "curve": None,           # the curve (if enabled) arrives next
        })
        del self.entries[HISTORY:]
        self.selected = None
        self.render_history()

    def on_curve(self, n, trig_index, tokens):
        points = []
        for tok in tokens:
            t, v = tok.split(":")
            points.append((int(t), int(v)))
        if len(points) < 2:
            return
        curve = {"points": points, "trig_index": trig_index,
                 "thr": self.applied_threshold}
        if self.entries:
            self.entries[0]["curve"] = curve   # belongs to the latest shutter
        self.current_curve = curve
        self.selected = None
        self.render_history()
        self.draw_curve()

    # ---------------------------------------------------------- history
    def render_history(self):
        for w in self.history_rows:
            w.destroy()
        self.history_rows = []
        if not self.entries:
            lbl = ctk.CTkLabel(
                self.history_box, text="No measurement yet", text_color=MUTED)
            lbl.pack(pady=14)
            self.history_rows.append(lbl)
            return
        mono = ctk.CTkFont(family="Courier", size=13)
        for i, e in enumerate(self.entries):
            text = f"{e['stamp']}   {e['speed']:>9}   {e['us'] / 1000:9.2f} ms"
            if e["curve"]:
                text += "   ∿"
            selected = (i == self.selected)
            btn = ctk.CTkButton(
                self.history_box, text=text, anchor="w", height=26, font=mono,
                corner_radius=8,
                fg_color=ACCENT if selected else "transparent",
                text_color="white" if selected else TEXT,
                hover_color=GRID,
                command=lambda idx=i: self.select_entry(idx),
            )
            btn.pack(fill="x", pady=1)
            self.history_rows.append(btn)

    def select_entry(self, idx):
        if idx >= len(self.entries):
            return
        self.selected = idx
        curve = self.entries[idx]["curve"]
        if curve:
            self.current_curve = curve
            self.draw_curve()
        self.render_history()

    # ------------------------------------------------------------ plot
    def draw_curve(self):
        c = self.plot
        bg = col(CARD)
        c.configure(bg=bg)
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        left, right, top, bottom = 38, 8, 8, 24
        pw, ph = w - left - right, h - top - bottom
        if pw < 20 or ph < 20:
            return
        curve = self.current_curve
        small = ("Helvetica", 8)

        if not curve:
            c.create_text(left + pw / 2, top + ph / 2, text="no curve yet",
                          fill=col(MUTED), font=("Helvetica", 11))
            return

        pts = curve["points"]
        t0, t1 = pts[0][0], pts[-1][0]
        span = max(t1 - t0, 1)
        # y axis: 0 to the next multiple of 10 above the highest value
        # (the trigger line is included so it always stays visible)
        top_value = max(max(v for _, v in pts), curve["thr"])
        vmax = (top_value // 10 + 1) * 10
        # grid step: smallest "nice" step giving at most 5 lines
        step = next(st for st in (10, 20, 50, 100,
                    200, 500, 1000) if vmax / st <= 5)

        def X(t):
            return left + (t - t0) / span * pw

        def Y(v):
            return top + ph - v / vmax * ph

        # grid and labels
        for v in range(0, vmax + 1, step):
            c.create_line(left, Y(v), left + pw, Y(v), fill=col(GRID))
            c.create_text(left - 5, Y(v), text=str(v),
                          anchor="e", fill=col(MUTED), font=small)
        for k in range(6):
            t = t0 + span * k / 5
            c.create_line(X(t), top, X(t), top + ph, fill=col(GRID))
            c.create_text(X(t), top + ph + 5, text=f"{t / 1000:.1f}", anchor="n",
                          fill=col(MUTED), font=small)
        c.create_text(left + pw, h - 1, text="ms",
                      anchor="se", fill=col(MUTED), font=small)

        # trigger time (t = 0) and trigger value line
        c.create_line(X(0), top, X(0), top + ph, fill=col(AXIS), dash=(4, 3))
        thr = curve["thr"]
        c.create_line(left, Y(thr), left + pw, Y(thr), fill=RED, width=2)
        c.create_text(left + pw - 3, Y(thr) - 3, text=f"trigger {thr}", anchor="se",
                      fill=RED, font=small)

        # curve
        coords = []
        for t, v in pts:
            coords += [X(t), Y(v)]
        c.create_line(*coords, fill=col(CURVE), width=2, smooth=False)
        for t, v in pts:
            c.create_oval(X(t) - 1.8, Y(v) - 1.8, X(t) + 1.8, Y(v) + 1.8,
                          fill=col(CURVE), outline="")

    # --------------------------------------------------------- commands
    def on_mode(self, label):
        self.mode = MODE_CODES[label]
        self.send(f"MODE {self.mode}")
        self.show_mode_view()

    def on_threshold(self):
        try:
            value = int(self.thr_var.get())
            if not 1 <= value <= 1023:
                raise ValueError
        except ValueError:
            self.say(
                "Trigger value must be a number between 1 and 1023", error=True)
            return
        self.applied_threshold = value
        self.send(f"THR {value}")

    def on_min_speed(self, label):
        self.send(f"MIN {SPEEDS[label]}")

    def on_curve_toggle(self):
        on = bool(self.curve_var.get())
        self.send(f"CURVE {int(on)}")
        if on:
            self.send(f"EXP {EXPECTED[self.expected_menu.get()]}")
        self.update_curve_ui()

    def on_expected(self, label):
        self.send(f"EXP {EXPECTED[label]}")


if __name__ == "__main__":
    ctk.set_appearance_mode("system")     # follows the OS light/dark setting
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    App(root)
    root.mainloop()
