from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from tkinter import messagebox

import customtkinter as ctk

from .. import theme as t
from .. import widgets as w
from ..agent import METRICS, SEVERITIES, Alert, Rule
from ..notify import SEVERITY_COLORS
from .base import Page, accent_button, ghost_button

METRIC_HELP = {
    "system_cpu": "Total CPU usage of the machine, 0-100.",
    "system_memory": "Physical memory in use, 0-100.",
    "swap": "Swap / page file usage, 0-100.",
    "disk_usage": "Fill level of the system drive, 0-100.",
    "process_count": "Number of running processes.",
    "process_cpu": "CPU of a single process (all cores = 100). Target narrows it to matching names.",
    "process_memory_mb": "Resident memory of a single process in MB.",
    "memory_growth_mb": "MB a process grew within the duration window. Good for spotting leaks.",
    "process_started": "Fires when a process matching the target appears. Target is required.",
    "process_stopped": "Fires when no process matching the target has run for the duration. Target is required.",
    "anomaly_system_cpu": "Learned baseline (EWMA). Threshold = how many standard deviations above normal, e.g. 4.",
    "anomaly_system_memory": "Learned baseline (EWMA). Threshold = standard deviations above normal memory use.",
    "anomaly_process_count": "Learned baseline (EWMA). Threshold = standard deviations above the usual process count.",
    "anomaly_network": "Learned baseline (EWMA). Threshold = standard deviations above normal network traffic.",
    "anomaly_disk_io": "Learned baseline (EWMA). Threshold = standard deviations above normal disk I/O.",
}


class StatChip(ctk.CTkFrame):
    def __init__(self, master, title: str, color: str) -> None:
        super().__init__(master, fg_color=t.CARD, corner_radius=12, border_width=1, border_color=t.BORDER)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(12, 0))
        ctk.CTkFrame(head, width=8, height=8, corner_radius=4, fg_color=color).pack(side="left")
        ctk.CTkLabel(head, text=title.upper(), font=w.FONTS.caption, text_color=t.MUTED,
                     height=16).pack(side="left", padx=8)
        self.value = ctk.CTkLabel(self, text="0", font=w.FONTS.h1, text_color=t.TEXT, anchor="w")
        self.value.pack(fill="x", padx=16)
        self.sub = ctk.CTkLabel(self, text="", font=w.FONTS.small, text_color=t.DIM, anchor="w", height=18)
        self.sub.pack(fill="x", padx=16, pady=(0, 12))


class AlertsPage(Page):
    title = "Alerts"
    subtitle = "A local watchdog agent checks every sample against your rules. Nothing leaves this machine."

    def __init__(self, master, app) -> None:
        super().__init__(master, app)
        self.mute = ctk.CTkSwitch(self.actions, text="Mute popups", command=self._toggle_mute,
                                  progress_color=t.AMBER, font=w.FONTS.body, text_color=t.MUTED)
        self.mute.pack(side="left", padx=(0, 14))
        ghost_button(self.actions, "Send test alert", self._test_alert, width=130).pack(side="left", padx=(0, 10))
        accent_button(self.actions, "Acknowledge all", self._ack_all, width=140).pack(side="left")

        body = self.body
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)

        chips = ctk.CTkFrame(body, fg_color="transparent")
        chips.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        for i in range(4):
            chips.grid_columnconfigure(i, weight=1, uniform="chips")
        self.chip_open = StatChip(chips, "Open now", t.ROSE)
        self.chip_unack = StatChip(chips, "Unacknowledged", t.AMBER)
        self.chip_day = StatChip(chips, "Last 24 hours", t.CYAN)
        self.chip_agent = StatChip(chips, "Agent", t.GREEN)
        for i, chip in enumerate((self.chip_open, self.chip_unack, self.chip_day, self.chip_agent)):
            chip.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0 if i == 3 else 8))

        lower = ctk.CTkFrame(body, fg_color="transparent")
        lower.grid(row=1, column=0, sticky="nsew")
        lower.grid_columnconfigure(0, weight=3, uniform="lower")
        lower.grid_columnconfigure(1, weight=2, uniform="lower")
        lower.grid_rowconfigure(0, weight=1)

        # -- feed ----------------------------------------------------------------
        feed = w.Panel(lower, "Alert feed", "Newest first. Double-click or press Enter to acknowledge.")
        feed.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ghost_button(feed.slot, "Acknowledge", self._ack_selected, width=110).pack(side="right")
        feed.body.grid_columnconfigure(0, weight=1)
        feed.body.grid_rowconfigure(0, weight=1)
        self.feed = w.DataTable(feed.body, height=10, border=False)
        self.feed.grid(row=0, column=0, sticky="nsew")
        self.feed.set_columns([
            ("time", "Time", 112, "w", False), ("severity", "Severity", 72, "w", False),
            ("rule", "Rule", 140, "w", False), ("message", "Message", 200, "w", True),
            ("status", "Status", 92, "w", False),
        ])
        tree = self.feed.tree
        for sev, color in SEVERITY_COLORS.items():
            tree.tag_configure(sev, foreground=color)
        tree.tag_configure("acked", foreground=t.DIM)
        tree.bind("<Double-1>", lambda _e: self._ack_selected())
        tree.bind("<Return>", lambda _e: self._ack_selected())

        # -- rules ---------------------------------------------------------------
        rules = w.Panel(lower, "Rules", "Stored in the alert_rules table")
        rules.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        rules.body.grid_columnconfigure(0, weight=1)
        rules.body.grid_rowconfigure(0, weight=1)
        self.rules = w.DataTable(rules.body, height=10, border=False)
        self.rules.grid(row=0, column=0, sticky="nsew")
        self.rules.set_columns([
            ("on", "On", 40, "center", False), ("name", "Name", 140, "w", False),
            ("rule", "Condition", 150, "w", True), ("severity", "Severity", 66, "w", False),
        ])
        rtree = self.rules.tree
        for sev, color in SEVERITY_COLORS.items():
            rtree.tag_configure(sev, foreground=color)
        rtree.tag_configure("off", foreground=t.DIM)
        rtree.bind("<Double-1>", lambda _e: self._edit_rule())

        buttons = ctk.CTkFrame(rules.body, fg_color="transparent")
        buttons.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        accent_button(buttons, "Add rule", self._add_rule, width=96).pack(side="left")
        ghost_button(buttons, "Edit", self._edit_rule, width=70).pack(side="left", padx=(8, 0))
        ghost_button(buttons, "On / Off", self._toggle_rule, width=80).pack(side="left", padx=(8, 0))
        ghost_button(buttons, "Delete", self._delete_rule, width=76).pack(side="left", padx=(8, 0))

        self._last_refresh = 0.0

    # -------------------------------------------------------------------------
    def on_show(self) -> None:
        self.refresh()

    def on_sample(self, sample) -> None:
        if self.visible and time.time() - self._last_refresh > 1.5:
            self.refresh()

    def on_alert(self, alert: Alert) -> None:
        if self.visible:
            self.refresh()

    def refresh(self) -> None:
        self._last_refresh = time.time()
        db = self.app.db
        counts = db.alert_counts()
        self.chip_open.value.configure(text=str(counts["open"]))
        self.chip_open.sub.configure(text="condition still true")
        self.chip_unack.value.configure(text=str(counts["unacknowledged"]))
        self.chip_unack.sub.configure(text=f"{counts['critical']} critical")
        self.chip_day.value.configure(text=str(counts["last_24h"]))
        self.chip_day.sub.configure(text="alerts raised")
        agent = self.app.agent
        if agent is None:
            self.chip_agent.value.configure(text="Off", text_color=t.DIM)
            self.chip_agent.sub.configure(text="started with --no-agent")
        elif agent.error:
            self.chip_agent.value.configure(text="Error", text_color=t.ROSE)
            self.chip_agent.sub.configure(text=str(agent.error)[:40])
        else:
            self.chip_agent.value.configure(text="Running", text_color=t.GREEN)
            self.chip_agent.sub.configure(
                text=f"{len(agent.rules)} rules, {agent.evaluated:,} checks, {self._webhook_count()} webhooks")

        items = []
        for aid, ts, severity, rule, message, resolved_at, acked in db.recent_alerts():
            status = "open" if resolved_at is None else "resolved"
            if acked:
                status += ", ack"
            items.append((str(aid), (
                datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S"), severity.upper(), rule, message, status,
            ), ("acked",) if acked else (severity,)))
        self.feed.sync(items)

        rule_items = []
        for row in db.load_rules():
            rule = Rule.from_row(row)
            rule_items.append((str(rule.id), (
                "ON" if rule.enabled else "off", rule.name, rule.describe(), rule.severity,
            ), (rule.severity,) if rule.enabled else ("off",)))
        self.rules.sync(rule_items)

    def _webhook_count(self) -> int:
        hooks = self.app.webhooks
        urls = {e.url for e in hooks.static_endpoints} if hooks else set()
        urls |= {url for _name, url, _sev in self.app.db.load_webhooks()}
        return len(urls) if hooks else 0

    # -- alerts ------------------------------------------------------------------
    def _toggle_mute(self) -> None:
        self.app.toasts.muted = bool(self.mute.get())

    def _ack_selected(self) -> None:
        iid = self.feed.selected()
        if iid:
            self.app.db.acknowledge(int(iid))
            self.refresh()
            self.app.refresh_alert_badge()

    def _ack_all(self) -> None:
        self.app.db.acknowledge(None)
        self.refresh()
        self.app.refresh_alert_badge()

    def _test_alert(self) -> None:
        now = time.time()
        message = "Notifications are working. This alert was created by the Send test alert button."
        aid = self.app.db.insert_alert(now, None, "Test alert", "test", "info", "Test alert",
                                       message, None, None, None)
        self.app.db.resolve_alert(aid, now)
        alert = Alert(aid, now, 0, "Test alert", "info", "Test alert", message)
        was_muted = self.app.toasts.muted
        self.app.toasts.muted = False
        self.app.toasts.show(alert)
        self.app.toasts.muted = was_muted
        if self.app.webhooks is not None:
            self.app.webhooks.submit("test", aid)  # test alerts ignore min_severity
        self.refresh()
        self.app.refresh_alert_badge()

    # -- rules -------------------------------------------------------------------
    def _selected_rule(self) -> Rule | None:
        iid = self.rules.selected()
        if not iid:
            return None
        row = next((r for r in self.app.db.load_rules() if r[0] == int(iid)), None)
        return Rule.from_row(row) if row else None

    def _rules_changed(self) -> None:
        if self.app.agent:
            self.app.agent.reload_rules()
        self.refresh()

    def _add_rule(self) -> None:
        RuleDialog(self, None)

    def _edit_rule(self) -> None:
        rule = self._selected_rule()
        if rule:
            RuleDialog(self, rule)

    def _toggle_rule(self) -> None:
        rule = self._selected_rule()
        if rule:
            self.app.db.set_rule_enabled(rule.id, not rule.enabled)
            self._rules_changed()

    def _delete_rule(self) -> None:
        rule = self._selected_rule()
        if rule and messagebox.askyesno("Delete rule", f"Delete the rule \"{rule.name}\"?\n"
                                        "Alerts it already raised are kept.", parent=self):
            self.app.db.delete_rule(rule.id)
            self._rules_changed()


class RuleDialog(ctk.CTkToplevel):
    def __init__(self, page: AlertsPage, rule: Rule | None) -> None:
        super().__init__(page, fg_color=t.SURFACE)
        self.page = page
        self.rule = rule
        self.title("Edit rule" if rule else "New rule")
        self.resizable(False, False)
        self.transient(page.winfo_toplevel())

        self.metric_keys = list(METRICS)
        self.metric_labels = [f"{METRICS[k][0]}  ({k})" for k in self.metric_keys]

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="both", expand=True, padx=22, pady=20)
        form.grid_columnconfigure(1, weight=1)

        def label(row, text):
            ctk.CTkLabel(form, text=text, font=w.FONTS.small, text_color=t.MUTED,
                         anchor="w").grid(row=row, column=0, sticky="w", pady=5, padx=(0, 14))

        def entry(row, value="", placeholder=""):
            e = ctk.CTkEntry(form, width=300, height=32, corner_radius=8, fg_color=t.CARD,
                             border_color=t.BORDER, text_color=t.TEXT, font=w.FONTS.body,
                             placeholder_text=placeholder)
            if value != "":
                e.insert(0, str(value))
            e.grid(row=row, column=1, sticky="ew", pady=5)
            return e

        label(0, "Name")
        self.name = entry(0, rule.name if rule else "")
        label(1, "Metric")
        self.metric = ctk.CTkOptionMenu(form, values=self.metric_labels, command=self._metric_changed,
                                        width=300, height=32, corner_radius=8, font=w.FONTS.body,
                                        fg_color=t.CARD, button_color=t.CARD_HOVER,
                                        button_hover_color=t.BORDER, text_color=t.TEXT,
                                        dropdown_fg_color=t.CARD, dropdown_text_color=t.TEXT,
                                        dropdown_hover_color=t.CARD_HOVER)
        self.metric.grid(row=1, column=1, sticky="ew", pady=5)
        self.help = ctk.CTkLabel(form, text="", font=w.FONTS.small, text_color=t.DIM, anchor="w",
                                 justify="left", wraplength=w.px(300))
        self.help.grid(row=2, column=1, sticky="w")
        label(3, "Target")
        self.target = entry(3, (rule.target or "") if rule else "", "optional, e.g. chrome*.exe")
        label(4, "Threshold")
        self.threshold = entry(4, f"{rule.threshold:g}" if rule else "80")
        label(5, "Duration (s)")
        self.duration = entry(5, f"{rule.duration_s:g}" if rule else "30")
        label(6, "Cooldown (s)")
        self.cooldown = entry(6, f"{rule.cooldown_s:g}" if rule else "600")
        label(7, "Severity")
        self.severity = ctk.CTkSegmentedButton(form, values=list(SEVERITIES), font=w.FONTS.small,
                                               selected_color=t.CYAN, unselected_color=t.CARD,
                                               selected_hover_color=t.mix(t.CYAN, "#ffffff", 0.2),
                                               unselected_hover_color=t.CARD_HOVER, fg_color=t.CARD,
                                               text_color=t.TEXT)
        self.severity.grid(row=7, column=1, sticky="w", pady=5)
        self.severity.set(rule.severity if rule else "warning")
        self.enabled = ctk.CTkSwitch(form, text="Enabled", progress_color=t.GREEN, font=w.FONTS.body,
                                     text_color=t.MUTED)
        self.enabled.grid(row=8, column=1, sticky="w", pady=(8, 0))
        if rule is None or rule.enabled:
            self.enabled.select()

        self.error = ctk.CTkLabel(form, text="", font=w.FONTS.small, text_color=t.ROSE, anchor="w")
        self.error.grid(row=9, column=0, columnspan=2, sticky="w", pady=(10, 0))
        buttons = ctk.CTkFrame(form, fg_color="transparent")
        buttons.grid(row=10, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ghost_button(buttons, "Cancel", self.destroy, width=90).pack(side="left", padx=(0, 10))
        accent_button(buttons, "Save rule", self._save, width=110).pack(side="left")

        key = rule.metric if rule and rule.metric in METRICS else "system_cpu"
        self.metric.set(self.metric_labels[self.metric_keys.index(key)])
        self._metric_changed(self.metric.get())
        self.after(50, self._focus)

    def _focus(self) -> None:
        try:
            self.lift()
            self.grab_set()
            self.name.focus_set()
        except Exception:
            pass

    def _metric_key(self) -> str:
        return self.metric_keys[self.metric_labels.index(self.metric.get())]

    def _metric_changed(self, _value) -> None:
        self.help.configure(text=METRIC_HELP.get(self._metric_key(), ""))

    def _save(self) -> None:
        name = self.name.get().strip()
        metric = self._metric_key()
        target = self.target.get().strip() or None
        try:
            threshold = float(self.threshold.get().replace(",", "."))
            duration = float(self.duration.get().replace(",", "."))
            cooldown = float(self.cooldown.get().replace(",", "."))
        except ValueError:
            self.error.configure(text="Threshold, duration and cooldown must be numbers.")
            return
        if not name:
            self.error.configure(text="Give the rule a name.")
            return
        if min(duration, cooldown) < 0:
            self.error.configure(text="Duration and cooldown cannot be negative.")
            return
        if METRICS[metric][1] == "watch" and not target:
            self.error.configure(text="This metric needs a target process name.")
            return
        try:
            self.page.app.db.save_rule(self.rule.id if self.rule else None, name, metric, threshold,
                                       duration, cooldown, self.severity.get(), target,
                                       bool(self.enabled.get()))
        except sqlite3.IntegrityError:
            self.error.configure(text="A rule with this name already exists.")
            return
        self.page._rules_changed()
        self.destroy()
