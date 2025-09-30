from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.app import App
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivymd.uix.card import MDCard


from .hue import (
    set_on,
    set_brightness,
    set_color_hs,  # light-level actions
    set_room_on,
    set_room_brightness,
    set_room_color_hs,  # room-level actions
)


class TappableCard(ButtonBehavior, FloatLayout):
    """A container that is tappable; it wraps a visual MDCard inside."""

    def on_touch_down(self, touch):
        # Let children (buttons, sliders, etc.) handle it first
        if FloatLayout.on_touch_down(self, touch):
            return True
        return ButtonBehavior.on_touch_down(self, touch)

    def on_touch_up(self, touch):
        if FloatLayout.on_touch_up(self, touch):
            return True
        return ButtonBehavior.on_touch_up(self, touch)


class HueTile(BoxLayout):
    item_id = NumericProperty(0)
    item_name = StringProperty("")
    is_on = BooleanProperty(False)
    brightness = NumericProperty(0)  # 0..100 in UI
    supports_color = BooleanProperty(False)
    show_open = BooleanProperty(False)  # only True for RoomTile
    tap_toggles = BooleanProperty(False)
    _busy = BooleanProperty(False)

    def on_card_tap(self):
        """Called when the card itself is tapped (via KV)."""
        if self.tap_toggles:
            self.toggle()

    def on_parent(self, *args):
        p = self.parent
        if p and hasattr(p, "col_default_width") and hasattr(p, "row_default_height"):
            p.bind(
                col_default_width=lambda *_: self._recalc_size(),
                row_default_height=lambda *_: self._recalc_size(),
            )
            self._recalc_size()

    def _recalc_size(self):
        p = self.parent
        if p and hasattr(p, "col_default_width") and hasattr(p, "row_default_height"):
            self.size_hint = (None, None)
            self.size = (p.col_default_width, p.row_default_height)

    def _notify(self, text):
        app = App.get_running_app()
        if app and hasattr(app, "show_message"):
            app.show_message(text, 2)

    # Default actions = per-light (used by LightTile)
    def toggle(self):
        if self._busy:
            return
        self._busy = True
        try:
            from .hue import light_is_on

            current_on = light_is_on(self.item_id)  # <-- real state
            new_state = not current_on
            set_on(self.item_id, new_state)
            self.is_on = new_state
            self._notify(f"{self.item_name}: {'ON' if new_state else 'OFF'}")
        except Exception as e:
            self._notify(f"Toggle error: {e}")
        finally:
            self._busy = False

    def on_slider_change(self, value):
        pass

    def on_slider_release(self, slider, touch):
        if self._busy or not slider.collide_point(*touch.pos):
            return
        self._busy = True
        try:
            bri_pct = int(slider.value)
            self.brightness = bri_pct
            set_brightness(self.item_id, bri_pct)
            self._notify(f"{self.item_name}: {bri_pct}%")
        except Exception as e:
            self._notify(f"Brightness error: {e}")
        finally:
            self._busy = False

    def open_color_picker(self):
        try:
            from kivy.uix.popup import Popup
            from kivy.uix.colorpicker import ColorPicker
            from kivy.utils import rgb_to_hsv

            cp = ColorPicker()

            def on_color(instance, color):
                h, s, v = rgb_to_hsv(*color[:3])
                set_color_hs(self.item_id, h * 360.0, s * 100.0)

            cp.bind(color=on_color)
            Popup(
                title=f"Color: {self.item_name}", content=cp, size_hint=(0.9, 0.9)
            ).open()
        except Exception as e:
            self._notify(f"Picker error: {e}")


class LightTile(HueTile):
    pass


class RoomTile(HueTile):
    show_open = BooleanProperty(True)
    tap_toggles = BooleanProperty(True)
    _busy = BooleanProperty(False)

    def open_details(self):
        app = App.get_running_app()
        if app and hasattr(app, "open_room"):
            app.open_room(self.item_id, self.item_name)

    def toggle(self):
        if self._busy:
            return
        self._busy = True
        try:
            from .hue import room_is_on

            current_on = room_is_on(self.item_id)  # <-- actual any_on/all_on
            new_state = not current_on
            set_room_on(self.item_id, new_state)
            self.is_on = new_state
            self._notify(f"{self.item_name}: {'ON' if new_state else 'OFF'}")

            # optional: refresh room list to sync sliders/labels
            from kivy.clock import Clock

            app = App.get_running_app()
            if app and app.root:
                main = app.root.get_screen("main")
                Clock.schedule_once(lambda *_: main.fetch_rooms_async(), 0.3)
        except Exception as e:
            self._notify(f"Toggle error: {e}")
        finally:
            self._busy = False

    def on_slider_release(self, slider, touch):
        if self._busy or not slider.collide_point(*touch.pos):
            return
        self._busy = True
        try:
            bri_pct = int(slider.value)
            self.brightness = bri_pct
            set_room_brightness(self.item_id, bri_pct)
            self._notify(f"{self.item_name}: {bri_pct}%")
        except Exception as e:
            self._notify(f"Brightness error: {e}")
        finally:
            self._busy = False

    def open_color_picker(self):
        try:
            from kivy.uix.popup import Popup
            from kivy.uix.colorpicker import ColorPicker
            from kivy.utils import rgb_to_hsv
            from .hue import set_room_color_hs

            cp = ColorPicker()

            def on_color(instance, color):
                h, s, v = rgb_to_hsv(*color[:3])
                set_room_color_hs(self.item_id, h * 360.0, s * 100.0)

            cp.bind(color=on_color)
            Popup(
                title=f"Color: {self.item_name}", content=cp, size_hint=(0.9, 0.9)
            ).open()
        except Exception as e:
            self._notify(f"Picker error: {e}")
