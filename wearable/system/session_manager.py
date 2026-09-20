"""
SessionManager -- shared by every module's run_standalone() to allow other
modules to be pulled into the background on demand, instead of either (a)
running only the one module you launched, or (b) running the full system
with everything on at once.

Core ideas:
  - The module you launched stays "primary": verbose, foreground, owns its
    own run loop exactly as before.
  - Anything activated afterward runs "in the background": quiet, polled
    on its own thread (continuous kind) or self-managed (service kind),
    readable on demand via telemetry/payload, but not printing every tick.
  - "switch" promotes a background module to primary and demotes the old
    one to background -- the old primary keeps running exactly like any
    other background module once demoted, no special-casing.
  - All command recognition is whole-utterance matched (see
    module_registry.find_module_by_alias) so ordinary sentences never
    false-trigger just because they contain a reserved word somewhere.
"""
import time
import threading
import importlib
from wearable.ui.terminal import display_on_terminal

print = display_on_terminal

from wearable.system.module_registry import (
    REGISTRY, MOD_CONTINUOUS, MOD_SERVICE, find_module_by_alias,
)

HISTORY_MAX = 12
CONTINUOUS_POLL_INTERVAL = 0.05


class SessionManager:
    def __init__(self, primary: str, lcd=None, gps=None):
        if primary not in REGISTRY:
            raise ValueError(f"Unknown primary module: {primary}")

        if lcd is None:
            from wearable.ui.display import Display
            self.lcd = Display()
            self._own_lcd = True
        else:
            self.lcd = lcd
            self._own_lcd = False

        self.home = primary
        self.primary = primary
        self.history = []  # stack of previous primaries, for 'switch back'

        self._instances = {}
        self._threads = {}
        self._stop_events = {}
        self._lock = threading.Lock()
        self.gps = gps

        self.activate(primary, quiet=True)

    # ------------------------------------------------------------------
    # Activation / deactivation
    # ------------------------------------------------------------------

    def _instantiate(self, name):
        entry = REGISTRY[name]
        if name == "gps" and self.gps is not None:
            return self.gps
        mod = importlib.import_module(entry["path"])
        cls = getattr(mod, entry["class"])
        if name == "bluetooth":
            return cls(lcd=self.lcd)
        if name == "lora":
            return cls(gps_receiver=self.gps)
        return cls()

    def activate(self, name, quiet=False):
        """Lazily instantiate and start a module in the background.
        Returns True if the module is active afterward (whether it was
        just started or already running)."""
        with self._lock:
            if name in self._instances:
                return True
            if name not in REGISTRY:
                return False

            entry = REGISTRY[name]
            try:
                instance = self._instantiate(name)

            except Exception as exc:
                import traceback 
                print(f"[Session] Failed to activate '{name}': {exc}")
                traceback.print_exc()

            self._instances[name] = instance

            if entry["kind"] == MOD_CONTINUOUS:
                stop_event = threading.Event()
                self._stop_events[name] = stop_event
                t = threading.Thread(
                    target=self._continuous_loop,
                    args=(name, instance, stop_event),
                    daemon=True,
                )
                self._threads[name] = t
                t.start()
            elif entry["kind"] == MOD_SERVICE:
                # Services that need caller-specific start arguments (e.g.
                # LoRa's on_packet_received callback) are NOT auto-started
                # here -- SessionManager has no context for what behavior
                # the caller wants. The caller starts those explicitly
                # after activate() returns. Only a plain no-argument
                # .start() is safe to call blindly.
                if hasattr(instance, "start") and not hasattr(instance, "start_listener"):
                    instance.start()
            # 'action' kind (camera): instantiate-only, nothing to start.

            if not quiet:
                self.lcd.log(f"{name.upper()} ON", "BACKGROUND", duration=1.5)
                print(f"[Session] Activated '{name}' in background.")

            return True

    def deactivate(self, name):
        """Stop and close a background module. Refuses on the current
        primary -- switch away from it first."""
        if name == self.primary:
            print(f"[Session] Cannot deactivate '{name}' -- it's the current primary.")
            return False

        with self._lock:
            return self._deactivate_locked(name)

    def _deactivate_locked(self, name):
        instance = self._instances.pop(name, None)
        if instance is None:
            return False

        if name in self._stop_events:
            self._stop_events[name].set()
            thread = self._threads.pop(name, None)
            if thread:
                thread.join(timeout=2.0)
            self._stop_events.pop(name, None)

        try:
            if hasattr(instance, "close"):
                instance.close()
        except Exception:
            pass

        print(f"[Session] Deactivated '{name}'.")
        return True

    def _continuous_loop(self, name, instance, stop_event):
        while not stop_event.is_set():
            try:
                instance.update()
            except Exception as e:
                print(f"[Session] '{name}' update error: {e}")
            time.sleep(CONTINUOUS_POLL_INTERVAL)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def is_active(self, name):
        return name in self._instances

    def active_modules(self):
        return list(self._instances.keys())

    def get(self, name):
        return self._instances.get(name)

    def payload_sources(self):
        """Active modules that can currently produce a sendable payload --
        feeds the LoRa send-selector menu."""
        return [n for n in self._instances if REGISTRY[n].get("payload")]

    def build_payload(self, name):
        """Runs the registered payload extractor for an active module.
        Returns (packet_type, data_dict) or (None, None) if unavailable."""
        instance = self._instances.get(name)
        entry = REGISTRY.get(name)
        if not instance or not entry or not entry.get("payload"):
            return None, None
        data = entry["payload"](instance)
        if data is None:
            return None, None
        return entry["payload_type"], data

    # ------------------------------------------------------------------
    # Switching
    # ------------------------------------------------------------------

    def switch(self, name, confirm_fn=None):
        """Promote `name` to primary, demoting the current primary to
        background (it keeps running, just quietly). Activates `name`
        first if needed. `confirm_fn(from_name, to_name) -> bool`, if
        given, gates the switch."""
        if name not in REGISTRY:
            print(f"[Session] Unknown module '{name}'.")
            return False
        if name == self.primary:
            print(f"[Session] '{name}' is already primary.")
            return False
        if not REGISTRY[name].get("switchable", False):
            print(f"[Session] '{name}' cannot be made primary.")
            return False

        if not self.is_active(name) and not self.activate(name, quiet=True):
            print(f"[Session] Could not activate '{name}' to switch to it.")
            return False

        if confirm_fn and not confirm_fn(self.primary, name):
            print("[Session] Switch cancelled.")
            return False

        self._push_history(self.primary)
        self._do_switch(name)
        return True

    def switch_back(self, confirm_fn=None):
        if not self.history:
            print("[Session] No previous primary to switch back to.")
            return False
        target = self.history.pop()
        return self._switch_direct(target, confirm_fn)

    def switch_home(self, confirm_fn=None):
        if self.primary == self.home:
            print("[Session] Already at home module.")
            return False
        return self._switch_direct(self.home, confirm_fn)

    def _switch_direct(self, name, confirm_fn=None):
        """Like switch(), but doesn't push onto history -- used by
        switch_back/switch_home so 'back' isn't itself pushed as an
        undo-able step."""
        if not self.is_active(name) and not self.activate(name, quiet=True):
            print(f"[Session] Could not activate '{name}'.")
            return False
        if confirm_fn and not confirm_fn(self.primary, name):
            print("[Session] Switch cancelled.")
            return False
        self._do_switch(name)
        return True

    def _do_switch(self, name):
        old_primary = self.primary
        self.primary = name
        self.lcd.log("SWITCHED TO", name.upper(), duration=2.0)
        print(f"[Session] Primary switched: {old_primary} -> {name}")

    def _push_history(self, name):
        # Dedupe consecutive repeats (A->B->A->B shouldn't pile up forever)
        # and cap length so 'back' stays meaningful over a long session.
        if self.history and self.history[-1] == name:
            self.history.pop()
            return
        self.history.append(name)
        if len(self.history) > HISTORY_MAX:
            self.history.pop(0)

    # ------------------------------------------------------------------
    # Command routing
    # ------------------------------------------------------------------

    def route_command(self, text, source="TYPED", confirm_fn=None):
        """Attempts to interpret `text` as a session-level command.

        Returns a short description string if handled, or None if `text`
        isn't a recognized command -- meaning the caller's own module
        should treat it as ordinary domain input (a LoRa message, a
        camera trigger, etc).

        Typed input must be prefixed with '/' to even be considered a
        command -- anything else is always literal text, no keyword
        collision possible (e.g. '/exit' is a command, 'exit' typed bare
        is just the word "exit" as a message).

        Voice input has no prefix, so it's matched whole-utterance only:
        a sentence merely containing a reserved word is never a command,
        only an utterance that *is* (exactly) a reserved word or alias.
        A message that must literally be a reserved word can be forced
        through with the spoken escape "message <word>", e.g. saying
        "message exit" sends the literal text "exit" instead of
        triggering the exit command.
        """
        raw = text.strip()

        if source == "TYPED":
            if not raw.startswith("/"):
                return None
            body = raw[1:].strip().lower()
        else:
            body = raw.lower()
            if body.startswith("message "):
                return None  # explicit escape -- always literal content

        if body in ("exit", "quit"):
            return "EXIT"
        if body == "menu":
            return "MENU"
        if body == "status":
            active = ", ".join(self.active_modules()) or "none"
            print(f"[Session] Primary={self.primary} | Active={active}")
            return "STATUS"
        if body in ("switch back", "switch-back"):
            self.switch_back(confirm_fn=confirm_fn)
            return "SWITCH_BACK"
        if body in ("switch home", "switch-home"):
            self.switch_home(confirm_fn=confirm_fn)
            return "SWITCH_HOME"
        if body.startswith("switch "):
            target = find_module_by_alias(body[len("switch "):])
            if target:
                self.switch(target, confirm_fn=confirm_fn)
                return f"SWITCH:{target}"
            return None

        target = find_module_by_alias(body)
        if target:
            self.activate(target)
            return f"ACTIVATE:{target}"

        return None

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self):
        """Tear down every active module, primary last, in reverse-
        activation order -- same cleanup guarantee run_full_system()'s
        finally block gives today, just generalized."""
        with self._lock:
            for name in list(self._instances.keys()):
                if name != self.primary:
                    self._deactivate_locked(name)
            if self.primary in self._instances:
                self._deactivate_locked(self.primary)

        if self._own_lcd:
            self.lcd.close()
