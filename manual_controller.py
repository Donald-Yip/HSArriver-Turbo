"""Command-line control for mulligan and player-turn actions."""

from dataclasses import dataclass, replace
import sys
import threading
import time
from typing import Callable, Optional, Union

from hslegend.adapters.legacy.card_queries import (
    allows_hand_target, hand_target_cardtype_mismatch,
)


class GlobalHotkeyInput:
    """Collect numeric commands globally while Hearthstone stays focused."""

    def __init__(self, keyboard_module, output_func=print,
                 shutdown_event=None, wait_timeout=0.05):
        self.keyboard = keyboard_module
        self.output = output_func
        self.shutdown_event = shutdown_event or threading.Event()
        self.wait_timeout = wait_timeout

    @staticmethod
    def _cancel_code(prompt: str) -> str:
        return "99" if "99取消" in prompt else "0"

    def __call__(self, prompt: str) -> str:
        buffer = []
        completed = threading.Event()
        result = {"value": ""}
        handles = []

        def show_buffer():
            current = "".join(buffer) or "（空）"
            self.output(f"当前输入：{current}")

        def append_digit(digit):
            buffer.append(digit)
            show_buffer()

        def backspace():
            if buffer:
                buffer.pop()
            show_buffer()

        def submit():
            result["value"] = "".join(buffer)
            completed.set()

        def cancel():
            result["value"] = self._cancel_code(prompt)
            self.output(f"已取消，提交 {result['value']}")
            completed.set()

        try:
            self.output(prompt)
            self.output(
                "炉石保持前台：直接按数字，Enter确认，Backspace删除，Esc取消。")
            for digit in "0123456789":
                handles.append(self.keyboard.add_hotkey(
                    digit, lambda value=digit: append_digit(value),
                    suppress=True))
            handles.append(self.keyboard.add_hotkey(
                "enter", submit, suppress=True))
            handles.append(self.keyboard.add_hotkey(
                "backspace", backspace, suppress=True))
            handles.append(self.keyboard.add_hotkey(
                "esc", cancel, suppress=True))

            while not completed.wait(self.wait_timeout):
                if self.shutdown_event.is_set():
                    raise KeyboardInterrupt
            return result["value"]
        finally:
            for handle in handles:
                try:
                    self.keyboard.remove_hotkey(handle)
                except Exception:
                    pass


def cancellable_console_input(
    prompt: str,
    stop_event: threading.Event,
    key_available=None,
    read_key=None,
    sleep_func=time.sleep,
    write_func=None,
) -> str:
    """Read a Windows console line while allowing Ctrl+Q to cancel it."""
    if key_available is None or read_key is None:
        import msvcrt
        key_available = msvcrt.kbhit
        read_key = msvcrt.getwch
    if write_func is None:
        write_func = lambda text: (sys.stdout.write(text), sys.stdout.flush())

    write_func(prompt)
    chars = []
    while not stop_event.is_set():
        if not key_available():
            sleep_func(0.05)
            continue
        char = read_key()
        if char in ("\r", "\n"):
            write_func("\n")
            return "".join(chars)
        if char == "\003":
            raise KeyboardInterrupt
        if char == "\b":
            if chars:
                chars.pop()
                write_func("\b \b")
            continue
        if char in ("\x00", "\xe0"):
            read_key()
            continue
        if char.isprintable():
            chars.append(char)
            write_func(char)
    raise KeyboardInterrupt


# Action DTOs are defined in the adapter layer. Keep this module as the stable
# root facade so existing callers retain identity-compatible imports.
from hslegend.adapters.legacy.actions import (
    ActionExecutionResult,
    ActionCancelled,
    AssembleChoiceAction,
    AttackAction,
    DiscoverChoiceAction,
    EndTurnAction,
    HeroPowerAction,
    LaunchStarshipAction,
    ManualAction,
    PlayCardAction,
    RefreshAction,
    SelectTargetAction,
    Target,
    TimelineAction,
    TradeCardAction,
    UseAbilityAction,
    UseLocationAction,
)

class ClickExecutor:
    """Translate validated actions into the project's click primitives."""

    def __init__(self, click_module=None, focus_func=None, sleep_func=None,
                 action_context=None, stop_event=None, choice_waiter=None):
        if click_module is None:
            import click as click_module
        self.click = click_module
        if action_context is None:
            namespace = getattr(click_module, "__dict__", {})
            action_context = namespace.get("hearthstone_action_session")
        if action_context is None:
            from contextlib import nullcontext
            action_context = nullcontext
        self.action_context = action_context
        if sleep_func is None:
            import time
            sleep_func = time.sleep
        self.stop_event = stop_event
        self.choice_waiter = choice_waiter
        self._sleep_func = sleep_func
        self.sleep = self._cancellable_sleep if stop_event is not None else sleep_func

    def _cancellable_sleep(self, seconds):
        seconds = max(0.0, float(seconds))
        if self.stop_event.wait(seconds):
            raise ActionCancelled("stopped")

    def _safe_action(self, action):
        cancellation = getattr(self.click, "action_cancellation", None)
        with self.action_context():
            if callable(cancellation):
                with cancellation(self.stop_event):
                    return self._run_safe(action)
            return self._run_safe(action)

    def _run_safe(self, action):
        try:
            return action()
        except ActionCancelled:
            raise
        except Exception:
            try:
                self.click.cancel_click()
            except Exception:
                pass
            raise

    def _click_target(self, target: Target, my_count: int, oppo_count: int):
        if target.side == "friendly":
            if target.kind == "hero":
                self.click.choose_my_hero()
            else:
                self.click.choose_my_minion(target.index, my_count)
        elif target.kind == "hero":
            self.click.choose_oppo_hero()
        else:
            self.click.choose_opponent_minion(target.index, oppo_count)

    def select_hand_card(self, hand_target_index: int, hand_count: int,
                         played_hand_index: Optional[int] = None):
        """点手牌扇形区里的第 hand_target_index 张（0 基）。

        所有「目标是自己的手牌」的卡都走这一个入口：战吼/法术打完牌之后手牌会
        少一张，所以牌面位置要减 1（played_hand_index 是打出去那张的位置）；
        地标本来就在场上，手牌数量不变，不传 played_hand_index。
        目标是手牌时 UI 出现得比普通随从目标晚，这里统一等 0.9s 再点。
        """
        self.sleep(0.9)
        screen_index = (hand_target_index - 1
                        if played_hand_index is not None
                        and played_hand_index < hand_target_index
                        else hand_target_index)
        self.click.choose_card(screen_index, hand_count)
        return screen_index

    def play_minion(self, hand_index, hand_count, gap_index, minion_count,
                    oppo_minion_count, target, choose_one=None,
                    settle_delay_seconds=0.0):
        return self._safe_action(lambda: self._play_minion(
            hand_index, hand_count, gap_index, minion_count,
            oppo_minion_count, target, choose_one, settle_delay_seconds))

    def _play_minion(self, hand_index, hand_count, gap_index, minion_count,
                     oppo_minion_count, target, choose_one=None,
                     settle_delay_seconds=0.0):
        # Keep the recommended placement even on a full board: the game
        # can resolve it as a Magnetic merge without adding a board slot.
        if choose_one is not None:
            self.click.cancel_click()
        self.click.choose_card(hand_index, hand_count)
        self.click.put_minion(gap_index, minion_count)
        if settle_delay_seconds > 0:
            self.sleep(settle_delay_seconds)
        self._select_choose_one(choose_one)
        # Let the board fan-out settle briefly before clicking the target.
        if target is not None:
            # Supported battlecry hand targets appear later than ordinary
            # minion targets, so wait for that UI to settle.
            is_friendly_hand_target = (
                target.side == "friendly" and target.kind == "hand")
            if not is_friendly_hand_target:
                self.sleep(0.4)
        if target is not None:
            adjusted = target
            if target.side == "friendly" and target.kind == "hand":
                # 手牌目标：打出这张牌后手牌少了一张，所以牌面位置要按「打出后」
                # 的数量算（手牌目标统一走 select_hand_card）。
                self.select_hand_card(target.index, hand_count - 1,
                                      played_hand_index=hand_index)
            elif (target.side == "friendly" and target.kind == "minion"
                  and target.index >= gap_index):
                adjusted = Target(
                    target.side, target.kind, target.index + 1,
                    target.entity_id)
                self._click_target(
                    adjusted, minion_count + 1, oppo_minion_count)
            else:
                self._click_target(
                    adjusted, minion_count + 1, oppo_minion_count)
        self.click.cancel_click()

    def play_spell(self, hand_index, hand_count, target, my_count,
                   oppo_count, choose_one=None, settle_delay_seconds=0.0):
        return self._safe_action(lambda: self._play_spell(
            hand_index, hand_count, target, my_count, oppo_count, choose_one,
            settle_delay_seconds))

    def _play_spell(self, hand_index, hand_count, target, my_count,
                    oppo_count, choose_one=None, settle_delay_seconds=0.0):
        if choose_one is not None:
            # Cancel a previous incomplete Choose One attempt before reopening
            # it. No cancel may occur between opening and choosing the option.
            self.click.cancel_click()
            self.click.choose_card(hand_index, hand_count)
            self.click.click_middle()
            if settle_delay_seconds > 0:
                self.sleep(settle_delay_seconds)
            self._select_choose_one(choose_one)
            if target is not None:
                self.sleep(0.3)
                self._click_target(target, my_count, oppo_count)
        elif target is None:
            if hand_count <= 2:
                # With a nearly empty hand, the fan re-centers noticeably.
                # Let the cards reach their final positions before selecting.
                self.sleep(0.3)
                self.click.choose_card(hand_index, hand_count)
                self.click.click_middle()
            else:
                self.click.choose_and_use_spell(hand_index, hand_count)
            if settle_delay_seconds > 0:
                self.sleep(settle_delay_seconds)
        else:
            self.click.choose_card(hand_index, hand_count)
            if settle_delay_seconds > 0:
                self.sleep(settle_delay_seconds)
            self._click_target(target, my_count, oppo_count)
        self.click.cancel_click()

    def play_location(self, hand_index, hand_count, gap_index,
                      board_slot_count):
        return self._safe_action(lambda: self._play_location(
            hand_index, hand_count, gap_index, board_slot_count))

    def _play_location(self, hand_index, hand_count, gap_index,
                       board_slot_count):
        self.click.choose_card(hand_index, hand_count)
        self.click.put_minion(gap_index, board_slot_count)
        self.click.cancel_click()

    def use_location(self, location_screen_index, board_slot_count, target,
                     my_board_count, oppo_board_count, my_hand_count=0):
        return self._safe_action(lambda: self._use_location(
            location_screen_index, board_slot_count, target,
            my_board_count, oppo_board_count, my_hand_count))

    def _use_location(self, location_screen_index, board_slot_count, target,
                      my_board_count, oppo_board_count, my_hand_count=0):
        self.click.choose_my_board_entity(
            location_screen_index, board_slot_count)
        if target is not None:
            self.sleep(0.3)
            if target.side == "friendly" and target.kind == "hand":
                # 守护巨龙之厅这类地标让你选一张自己的手牌：地标已在场上，
                # 手牌数量就是当前手牌数（不像出随从那样要先减去自己）。
                self.select_hand_card(target.index, my_hand_count)
            else:
                self._click_target(target, my_board_count, oppo_board_count)
        self.click.cancel_click()

    def launch_starship(self, starship_screen_index, board_count):
        return self._safe_action(lambda: self._launch_starship(
            starship_screen_index, board_count))

    def _launch_starship(self, starship_screen_index, board_count):
        self.click.choose_my_board_entity(
            starship_screen_index, board_count)
        # 第一次点击只是把组件面板展开，面板里的「发射」按钮要等它画完，
        # 立刻点会点空（实测约 0.8s 才稳定）。
        self.sleep(0.8)
        self.click.click_launch_starship()

    def play_weapon(self, hand_index, hand_count):
        return self._safe_action(
            lambda: self._play_weapon(hand_index, hand_count))

    def _play_weapon(self, hand_index, hand_count):
        self.click.choose_and_use_spell(hand_index, hand_count)
        self.click.cancel_click()

    def trade_card(self, hand_index, hand_count):
        return self._safe_action(
            lambda: self._trade_card(hand_index, hand_count))

    def _trade_card(self, hand_index, hand_count):
        self.click.choose_card(hand_index, hand_count)
        self.click.drag_card_to_deck()

    def use_hero_power(self, target=None, my_count=0, oppo_count=0,
                       choose_one=None, hero_power_slot="primary"):
        return self._safe_action(lambda: self._use_hero_power(
            target, my_count, oppo_count, choose_one, hero_power_slot))

    def _use_hero_power(self, target, my_count, oppo_count, choose_one=None,
                        hero_power_slot="primary"):
        if choose_one is not None:
            self.click.cancel_click()
            self._click_hero_power(hero_power_slot)
            self._select_choose_one(choose_one)
            if target is not None:
                self.sleep(0.3)
                self._click_target(target, my_count, oppo_count)
            self.click.cancel_click()
            return
        if target is None:
            self._use_hero_power_no_point(hero_power_slot)
            return
        self._click_hero_power(hero_power_slot)
        self._click_target(target, my_count, oppo_count)
        self.click.cancel_click()

    def _select_choose_one(self, choice):
        if choice is not None:
            if (self.choice_waiter is not None
                    and choice.choice_entity_id is None
                    and choice.choice_card_id is not None):
                ready = self.choice_waiter(
                    choice.choice_parent_entity_id,
                    choice.choice_card_id,
                )
                if not ready:
                    raise RuntimeError("choose_one_options_pending")
            self.sleep(0.3)
            self.click.choose_discover_card(choice.choice_index, choice.choice_count)

    def _click_hero_power(self, slot):
        if slot == "primary":
            return self.click.click_skill()
        return self.click.click_skill(slot)

    def _use_hero_power_no_point(self, slot):
        if slot == "primary":
            return self.click.use_skill_no_point()
        return self.click.use_skill_no_point(slot)

    def choose_assemble_card(self, choice_index):
        return self._safe_action(
            lambda: self.click.choose_assemble_card(choice_index))

    def choose_discover_card(self, choice_index, choice_count, target=None,
                             my_count=0, oppo_count=0, choice_delay=None):
        """点选项；带 target 时接着把目标也点掉（一个完整手势）。

        只传前两个参数时行为与改动前完全一致（只点选项）。带 target 时：
        点选项 → 等 choice_delay（选项 UI 收起、目标高亮出现）→ 点目标 → 右键取消。
        少了这一步，像鲍勃「招募随从」那样"选完选项还要点一个对方随从"的牌会
        停在半途：游戏在等你点，脚本却以为做完了。
        """
        return self._safe_action(lambda: self._choose_discover_card(
            choice_index, choice_count, target, my_count, oppo_count,
            choice_delay))

    def _choose_discover_card(self, choice_index, choice_count, target,
                              my_count, oppo_count, choice_delay):
        self.click.choose_discover_card(choice_index, choice_count)
        if target is None:
            return
        delay = (0.3 if choice_delay is None else float(choice_delay))
        if delay > 0:
            self.sleep(delay)
        self._click_target(target, my_count, oppo_count)
        self.click.cancel_click()

    def use_ability(self, source, board_count, choice_index, choice_count,
                    target=None, oppo_count=0, target_delay=0.3):
        """古神/泰坦：点场上随从 → 点技能牌 →（可选）点目标。"""
        return self._safe_action(lambda: self._use_ability(
            source, board_count, choice_index, choice_count, target,
            oppo_count, target_delay))

    def _use_ability(self, source, board_count, choice_index, choice_count,
                     target, oppo_count, target_delay):
        # 上一次没点完的面板先右键收掉：面板是模态的，右键只会关面板/取消
        # 悬空的选择，不会动到场面。
        self.click.cancel_click()
        self.click.choose_my_board_entity(source.index, board_count)
        # 技能面板要画完才能点里面的牌（实测约 0.5~0.8s，与星舰组件面板同口径）。
        self.sleep(0.8)
        self.click.choose_skill_card(choice_index, choice_count)
        if target is not None:
            delay = 0.3 if target_delay is None else float(target_delay)
            if delay > 0:
                self.sleep(delay)
            self._click_target(target, board_count, oppo_count)
        # 故意不右键：克苏恩的凝视这类技能点完选项还在等目标，右键会把这次
        # 选择取消掉（盒子随后会打印「选择对方N号位卡牌」，由 SELECT_TARGET 接着点）。

    def select_target(self, target, my_count=0, oppo_count=0, hand_count=0):
        """只点一个目标（选项/发现结算后的「选择对方N号位卡牌」）。

        与 `choose_discover_card` 带 target 的路径不同：这里没有选项要再点，
        游戏已经停在“请选择目标”，所以只左键点目标、**不右键取消**（右键会把
        这次目标选择撤销，等于白点）。手牌目标从手牌扇形区点。
        """
        return self._safe_action(
            lambda: self._select_target(target, my_count, oppo_count, hand_count))

    def _select_target(self, target, my_count, oppo_count, hand_count):
        if target.kind == "hand":
            if target.index is None or not 0 <= target.index < hand_count:
                raise ValueError("hand_target_out_of_range")
            # 手牌区域的牌比场上目标晚一点出现，给 UI 一点收起时间。
            self.sleep(0.3)
            self.click.choose_card(target.index, hand_count)
            return
        self._click_target(target, my_count, oppo_count)

    def attack(self, attacker, target, my_count, oppo_count):
        return self._safe_action(
            lambda: self._attack(attacker, target, my_count, oppo_count))

    def _attack(self, attacker, target, my_count, oppo_count):
        if attacker.kind == "minion":
            if target.kind == "minion":
                self.click.minion_beat_minion(
                    attacker.index, my_count, target.index, oppo_count)
            else:
                self.click.minion_beat_hero(attacker.index, my_count)
        elif target.kind == "minion":
            self.click.hero_beat_minion(target.index, oppo_count)
        else:
            self.click.hero_beat_hero()

    def end_turn(self):
        return self._safe_action(self._end_turn)

    def _end_turn(self):
        self.click.end_turn()

    def timeline_click(self, choice):
        return self._safe_action(lambda: self._timeline_click(choice))

    def choose_timeline(self, card_id):
        return self._safe_action(lambda: self.click.choose_timeline(card_id))

    def _timeline_click(self, choice):
        if choice == "undo":
            self.click.click_timeline_undo()
        else:
            self.click.click_timeline_keep()


class ManualController:
    def __init__(
        self,
        input_func: Callable[[str], str] = input,
        output_func: Callable[[str], None] = print,
        executor=None,
    ):
        self.input = input_func
        self.output = output_func
        self.executor = executor

    def _read_int(self, prompt: str) -> Optional[int]:
        try:
            return int(self.input(prompt).strip())
        except (ValueError, TypeError):
            self.output("无效输入，请输入一个整数。")
            return None

    @staticmethod
    def mulligan_is_current(original_state, fresh_state) -> bool:
        """Ensure hand positions still refer to the cards shown to the user."""
        original_ids = [
            (card.card_id, getattr(card, "entity_id", None))
            for card in original_state.my_hand_cards
        ]
        fresh_ids = [
            (card.card_id, getattr(card, "entity_id", None))
            for card in fresh_state.my_hand_cards
        ]
        return (not getattr(fresh_state, "is_end", False)
                and getattr(fresh_state, "game_num_turns_in_play", 0) == 0
                and original_ids == fresh_ids)

    def choose_mulligan(self, state) -> list[int]:
        replaceable = [
            card for card in state.my_hand_cards
            if not getattr(card, "is_coin", False)
        ]
        self.output("=== 起手留牌 ===")
        for index, card in enumerate(replaceable, start=1):
            self.output(f"[{index}] {card.name}  费用: {card.current_cost}")

        chosen: list[int] = []
        while True:
            value = self._read_int("输入要替换的手牌编号，输入 0 停止并确认：")
            if value is None:
                continue
            if value == 0:
                return chosen
     

            replaceable = [
                index for index, card in enumerate(state.my_hand_cards)
                if not getattr(card, "is_coin", False)]
            if not 1 <= value <= len(replaceable):
                self.output("无效手牌编号，请重新输入。")
                continue
            slot = value - 1
            if slot in chosen:
                self.output("该手牌已经选择，请重新输入。")
                continue
            chosen.append(slot)

    def prompt_turn_action(self, state):
        while True:
            self.output("可执行操作：")
            self.output("  0：刷新战局状态")
            self.output("  1-10：选择手牌")
            self.output("  11：使用英雄技能")
            self.output("  12：随从攻击")
            self.output("  13：结束回合")
            self.output("  14：使用地标")
            self.output("  15：发现选择")
            value = self._read_int("请输入操作编号：")
            if value is None:
                continue
            if value == 0:
                return RefreshAction()
            if value == 11:
                return HeroPowerAction()
            if value == 12:
                action = self._prompt_attack(state)
                if action is None:
                    continue
                return action
            if value == 13:
                return EndTurnAction()
            if value == 14:
                locations = getattr(state, "my_locations", [])
                if not locations:
                    self.output("没有可用的地标。")
                    continue
                for index, location in enumerate(locations, start=1):
                    self.output(f"[{index}] {location.name}")
                while True:
                    location_number = self._read_int(
                        "选择要使用的地标编号，99 取消：")
                    if location_number is None:
                        continue
                    if location_number == 99:
                        break
                    if 1 <= location_number <= len(locations):
                        location = locations[location_number - 1]
                        target = self._read_target(state)
                        if target == "cancel":
                            break
                        return UseLocationAction(
                            location_index=location_number - 1,
                            card_id=location.card_id,
                            location_entity_id=getattr(
                                location, "entity_id", None),
                            target=target,
                        )
                    self.output("无效地标编号，请重新输入。")
                continue
            if value == 15:
                choice_count = getattr(
                    state, "discover_choice_count", 0)
                if choice_count not in (1, 2, 3, 4):
                    self.output("当前没有发现选择。")
                    continue
                while True:
                    choice_index = self._read_int(
                        f"选择发现选项（1-{choice_count}，99 取消）：")
                    if choice_index is None:
                        continue
                    if choice_index == 99:
                        break
                    if 1 <= choice_index <= choice_count:
                        return DiscoverChoiceAction(
                            choice_index - 1, choice_count)
                    self.output("无效选项编号，请重新输入。")
                continue
            if not 1 <= value <= len(state.my_hand_cards):
                self.output("无效操作编号，请重新输入。")
                continue

            hand_index = value - 1
            selected = state.my_hand_cards[hand_index]
            cardtype = selected.cardtype
            if cardtype == "MINION":
                board_slot_count = getattr(
                    state, "my_board_slot_num", len(state.my_minions))
                while True:
                    gap = self._read_int(
                        f"随从落点（1-{board_slot_count + 1}，99取消）："
                    )
                    if gap is None:
                        continue
                    if gap == 99:
                        break
                    if 1 <= gap <= board_slot_count + 1:
                        target = self._read_target(state)
                        if target == "cancel":
                            break
                        return PlayCardAction(
                            hand_index, selected.card_id, cardtype,
                            gap_index=gap - 1, target=target,
                            hand_entity_id=getattr(
                                selected, "entity_id", None),
                        )
                    self.output("无效落点编号，请重新输入。")
                continue
            if cardtype == "LOCATION":
                board_slot_count = getattr(
                    state, "my_board_slot_num", len(state.my_minions))
                while True:
                    gap = self._read_int(
                        f"地标落点（1-{board_slot_count + 1}，99取消）："
                    )
                    if gap is None:
                        continue
                    if gap == 99:
                        break
                    if 1 <= gap <= board_slot_count + 1:
                        return PlayCardAction(
                            hand_index, selected.card_id, cardtype,
                            gap_index=gap - 1,
                            hand_entity_id=getattr(
                                selected, "entity_id", None),
                        )
                    self.output("无效落点编号，请重新输入。")
                continue
            if cardtype == "SPELL":
                target = self._read_target(state)
                if target == "cancel":
                    continue
                return PlayCardAction(
                    hand_index, selected.card_id, cardtype, target=target,
                    hand_entity_id=getattr(selected, "entity_id", None),
                )
            if cardtype == "WEAPON":
                return PlayCardAction(
                    hand_index, selected.card_id, cardtype,
                    hand_entity_id=getattr(selected, "entity_id", None))

            self.output(f"暂不支持卡牌类型：{cardtype}")

    def _prompt_attack(self, state):
        if not state.my_minions:
            self.output("己方场上没有随从可以攻击。")
            return None
        for index, minion in enumerate(state.my_minions, start=1):
            self.output(f"[{index}] {minion.name}")
        while True:
            attacker_index = self._read_int("选择攻击者编号，99 取消：")
            if attacker_index is None:
                continue
            if attacker_index == 99:
                return None
            if 1 <= attacker_index <= len(state.my_minions):
                attacker = Target("friendly", "minion", attacker_index - 1)
                target = self._read_target(state)
                if target == "cancel":
                    return None
                return AttackAction(attacker, target)
            self.output("无效攻击者编号，请重新输入。")

    def _read_target(self, state):
        self.output("目标编号：")
        self.output("  0：无目标 | 8/18：敌方英雄 | 9：我方英雄")
        self.output("  1-7：敌方随从 | 10-16：我方随从 | 99：取消")
        while True:
            value = self._read_int("请输入目标编号：")
            if value is None:
                continue
            if value == 0:
                return None
            if value in (8, 18):
                return Target("enemy", "hero", None)
            if value == 9:
                return Target("friendly", "hero", None)
            if 1 <= value <= 7:
                index = value - 1
                if index < len(state.oppo_minions):
                    return Target(
                        "enemy", "minion", index,
                        getattr(state.oppo_minions[index],
                                "entity_id", None))
                return Target("enemy", "minion", index)
            if 10 <= value <= 16:
                index = value - 10
                if index < len(state.my_minions):
                    return Target(
                        "friendly", "minion", index,
                        getattr(state.my_minions[index],
                                "entity_id", None))
                return Target("friendly", "minion", index)
            if value == 99:
                return "cancel"
            self.output("无效目标编号，请重新输入。")



    @staticmethod
    def _target_exists(target: Optional[Target], state) -> bool:
        if target is None:
            return True
        if target.kind == "hero":
            entity = (state.my_hero if target.side == "friendly"
                      else state.oppo_hero if target.side == "enemy"
                      else None)
            return (entity is not None
                    and (target.entity_id is None
                         or getattr(entity, "entity_id", None)
                         == target.entity_id))
        if target.kind == "hand":
            if target.side != "friendly" or target.index is None:
                return False
            collection = state.my_hand_cards
            if not 0 <= target.index < len(collection):
                return False
            return (target.entity_id is None
                    or getattr(collection[target.index], "entity_id", None)
                    == target.entity_id)
        if target.kind != "minion" or target.index is None:
            return False
        collection = (state.my_minions if target.side == "friendly"
                      else state.oppo_minions if target.side == "enemy"
                      else [])
        if not 0 <= target.index < len(collection):
            return False
        return (target.entity_id is None
                or getattr(collection[target.index], "entity_id", None)
                == target.entity_id)

    @staticmethod
    def _hero_power_exists(action: HeroPowerAction, state) -> bool:
        powers = list(getattr(state, "my_hero_powers", None) or ())
        if not powers:
            fallback = getattr(state, "my_hero_power", None)
            if fallback is not None:
                powers = [fallback]
        wanted_entity = getattr(action, "hero_power_entity_id", None)
        wanted_card = getattr(action, "card_id", None)
        if wanted_entity is None and wanted_card is None:
            return True
        for power in powers:
            if (wanted_entity is not None
                    and getattr(power, "entity_id", None) != wanted_entity):
                continue
            if (wanted_card is not None
                    and getattr(power, "card_id", None) != wanted_card):
                continue
            return True
        return False

    def _reject(self, message: str) -> ActionExecutionResult:
        self.output(message)
        return ActionExecutionResult(False, message)

    @staticmethod
    def _board_slot_count(state, side: str) -> int:
        if side == "friendly":
            entities = (list(getattr(state, "my_minions", ()) or ())
                        + list(getattr(state, "my_locations", ()) or ()))
            fallback = getattr(state, "my_board_slot_num", len(entities))
        else:
            entities = (list(getattr(state, "oppo_minions", ()) or ())
                        + list(getattr(state, "oppo_locations", ()) or ()))
            fallback = getattr(state, "oppo_board_slot_num", len(entities))
        positions = {getattr(entity, "zone_pos", 0) for entity in entities}
        positions.discard(0)
        return len(positions) if positions else int(fallback)

    @staticmethod
    def _target_for_click(target: Optional[Target], state) -> Optional[Target]:
        if target is None or target.kind != "minion" or target.index is None:
            return target
        collection = (state.my_minions if target.side == "friendly"
                      else state.oppo_minions)
        entity = collection[target.index]
        zone_pos = getattr(entity, "zone_pos", 0)
        screen_index = zone_pos - 1 if zone_pos > 0 else target.index
        return Target(
            target.side, target.kind, screen_index, target.entity_id)

    @staticmethod
    def bind_to_turn(action: ManualAction, state) -> ManualAction:
        return replace(
            action, turn_number=state.game_num_turns_in_play)

    @staticmethod
    def _choice_entity_ok(action, state) -> bool:
        """抉择/发现选项的实体 id 还在原位吗（位置变了就别点）。

        只认绑定的那个父实体；父实体不在当前日志里（过期条目）时退回
        任意父实体的同号位，只在"确实查过且有冲突"时才拒绝。
        """
        wanted = action.choice_entity_id
        wanted_card_id = getattr(action, "choice_card_id", None)
        if wanted is None and wanted_card_id is None:
            return True
        parent_id = action.choice_parent_entity_id
        if parent_id is not None:
            parent = (getattr(state, "power_options", {}) or {}).get(parent_id)
            if parent is not None:
                choice = (parent.get("choices") or {}).get(action.choice_index)
                if choice is None:
                    return True
                if wanted is not None and choice.get("entity_id") != wanted:
                    return False
                if (wanted_card_id is not None
                        and choice.get("card_id") != wanted_card_id):
                    return False
        for option in (getattr(state, "power_options", {}) or {}).values():
            choice = (option.get("choices") or {}).get(action.choice_index)
            if choice is not None:
                if wanted is not None and choice.get("entity_id") != wanted:
                    return False
                if (wanted_card_id is not None
                        and choice.get("card_id") != wanted_card_id):
                    return False
        # 发现/选择一项菜单（通用选择）走的是另一份映射。
        general = getattr(state, "general_choice_entity_ids", {}) or {}
        if (wanted is not None and action.choice_index in general
                and general[action.choice_index] != wanted):
            return False
        return True

    @staticmethod
    def _target_label(target) -> str:
        side = "己方" if target.side == "friendly" else "对方"
        if target.kind == "hero":
            return f"目标：{side}英雄"
        position = (target.index + 1) if target.index is not None else "?"
        return f"目标：{side}{position}号位"

    @staticmethod
    def _choice_target_delay() -> float:
        """点完选项到点目标之间的等待（秒）：选项 UI 收起、目标高亮出现。"""
        try:
            from src.recommendation_config import RecommendationConfig
            return float(RecommendationConfig().discover_target_delay_seconds)
        except Exception:
            return 0.3

    def execute(self, action: ManualAction, state) -> ActionExecutionResult:
        try:
            return self._execute(action, state)
        except ActionCancelled:
            self.output("已收到停止指令，鼠标动作立即中止。")
            return ActionExecutionResult(
                False, "已收到停止指令。", cancelled=True)
        except Exception as exc:
            message = f"鼠标操作失败，未执行或无法确认：{exc}"
            self.output(message)
            return ActionExecutionResult(False, message, recovery_needed=True)

    def _execute(self, action: ManualAction, state) -> ActionExecutionResult:
        if isinstance(action, RefreshAction):
            return ActionExecutionResult(False, "已刷新战局状态。")
        if not getattr(state, "is_my_turn", False):
            return self._reject("当前不是己方回合，未执行操作。")
        if (action.turn_number is not None
                and action.turn_number != state.game_num_turns_in_play):
            return self._reject("回合已经变化，旧操作未执行。")
        if self.executor is None:
            return self._reject("没有可用的鼠标执行器。")

        if isinstance(action, TradeCardAction):
            if not 0 <= action.hand_index < len(state.my_hand_cards):
                return self._reject("手牌位置已经失效，未执行交易。")
            selected = state.my_hand_cards[action.hand_index]
            if (selected.card_id != action.card_id
                    or selected.cardtype != action.cardtype):
                return self._reject("手牌内容已经变化，未执行交易。")
            if (action.hand_entity_id is not None
                    and getattr(selected, "entity_id", None)
                    != action.hand_entity_id):
                return self._reject("手牌对象已经变化，未执行交易。")
            self.executor.trade_card(
                action.hand_index, len(state.my_hand_cards))
            return ActionExecutionResult(True, f"已交易手牌：{selected.name}")

        if isinstance(action, PlayCardAction):
            if not 0 <= action.hand_index < len(state.my_hand_cards):
                return self._reject("手牌位置已经失效，未执行操作。")
            selected = state.my_hand_cards[action.hand_index]
            if (selected.card_id != action.card_id
                    or selected.cardtype != action.cardtype):
                return self._reject("手牌内容已经变化，未执行操作。")
            if (action.hand_entity_id is not None
                    and getattr(selected, "entity_id", None)
                    != action.hand_entity_id):
                return self._reject("手牌对象已经变化，未执行操作。")
            is_hand_target = (
                action.target is not None and action.target.kind == "hand")
            hand_target_label = None
            if is_hand_target:
                if (not allows_hand_target(selected.card_id)
                        or action.target.side != "friendly"
                        or action.target.index is None):
                    return self._reject(
                        "该随从不能选择手牌目标，未执行操作。")
                if (action.target.index == action.hand_index
                        or not self._target_exists(action.target, state)):
                    return self._reject(
                        "手牌目标已经失效，未执行操作。")
                # 卡面要求具体类型（「一张法术牌」…）时，号位那张牌必须对得上，
                # 否则这一次点击会点错牌，宁可拒绝重试。
                target_card = state.my_hand_cards[action.target.index]
                if hand_target_cardtype_mismatch(selected.card_id, target_card):
                    return self._reject(
                        "手牌目标类型不符合该卡要求，未执行操作。")
                hand_target_label = (
                    f"选手中第 {action.target.index + 1} 号位"
                    f"（{getattr(target_card, 'name', '?')}）")
            elif not self._target_exists(action.target, state):
                return self._reject("目标已经不存在，未执行操作。")

            choice_args = ({"choose_one": action.choose_one}
                           if action.choose_one is not None else {})
            settle_args = ({"settle_delay_seconds": action.settle_delay_seconds}
                           if getattr(action, "settle_delay_seconds", 0) else {})
            if action.cardtype == "MINION":
                my_board_count = self._board_slot_count(state, "friendly")
                oppo_board_count = self._board_slot_count(state, "enemy")
                if action.gap_index is None or not (
                        0 <= action.gap_index <= my_board_count):
                    return self._reject("随从落点已经失效，未执行操作。")
                self.executor.play_minion(
                    action.hand_index, len(state.my_hand_cards),
                    action.gap_index, my_board_count,
                    oppo_board_count,
                    self._target_for_click(action.target, state),
                    **choice_args, **settle_args,
                )
            elif action.cardtype == "SPELL":
                self.executor.play_spell(
                    action.hand_index, len(state.my_hand_cards),
                    self._target_for_click(action.target, state),
                    self._board_slot_count(state, "friendly"),
                    self._board_slot_count(state, "enemy"),
                    **choice_args, **settle_args,
                )
            elif action.cardtype == "WEAPON":
                self.executor.play_weapon(action.hand_index,
                                          len(state.my_hand_cards))
            elif action.cardtype == "HERO":
                self.executor.play_spell(
                    action.hand_index, len(state.my_hand_cards), None,
                    self._board_slot_count(state, "friendly"),
                    self._board_slot_count(state, "enemy"),
                )
            elif action.cardtype == "LOCATION":
                my_board_count = self._board_slot_count(state, "friendly")
                if my_board_count >= 7:
                    return self._reject("己方场上已满，未执行操作。")
                if action.target is not None:
                    return self._reject("地标牌不应指定目标，未执行操作。")
                if action.gap_index is None or not (
                        0 <= action.gap_index <= my_board_count):
                    return self._reject("地标落点已经失效，未执行操作。")
                self.executor.play_location(
                    action.hand_index, len(state.my_hand_cards),
                    action.gap_index, my_board_count,
                )
            else:
                return self._reject(f"不支持卡牌类型：{action.cardtype}")
            detail = f"已执行手牌：{selected.name}"
            if hand_target_label:
                detail += f"，{hand_target_label}"
            return ActionExecutionResult(True, detail)

        if isinstance(action, HeroPowerAction):
            if not self._hero_power_exists(action, state):
                return self._reject("英雄技能已经变化，未执行操作。")
            choice_args = ({"choose_one": action.choose_one}
                           if action.choose_one is not None else {})
            if action.target is None:
                self.executor.use_hero_power(
                    hero_power_slot=action.hero_power_slot, **choice_args)
            else:
                if not self._target_exists(action.target, state):
                    return self._reject(
                        "英雄技能目标已经不存在，未执行操作。")
                self.executor.use_hero_power(
                    self._target_for_click(action.target, state),
                    self._board_slot_count(state, "friendly"),
                    self._board_slot_count(state, "enemy"),
                    hero_power_slot=action.hero_power_slot,
                    **choice_args,
                )
            return ActionExecutionResult(True, "已使用英雄技能。")

        if isinstance(action, AssembleChoiceAction):
            if not 0 <= action.choice_index < 4:
                return self._reject("拼装选项位置已经失效，未执行操作。")
            if (action.choice_entity_id is not None
                    and not self._choice_entity_ok(action, state)):
                return self._reject("拼装选项已经变化，未执行操作。")
            self.executor.choose_assemble_card(action.choice_index)
            return ActionExecutionResult(
                True, f"已选择拼装选项：{action.choice_index + 1}号位")

        if isinstance(action, DiscoverChoiceAction):
            if action.timeline_card_id is not None:
                # 时间线选择：盒子的推荐可能只写「选择我方N号位卡牌」，得靠
                # 日志里的选项卡牌 ID 确认这确实是回溯/维持，且位置没变。
                choices = getattr(state, "general_choice_cards", {})
                if (getattr(state, "discover_choice_count", None) != 2
                        or set(choices) != {0, 1}
                        or set(choices.values()) != {"TIME_000ta", "TIME_000tb"}
                        or choices.get(action.choice_index)
                        != action.timeline_card_id):
                    return self._reject("时间线选项已变化，未执行操作。")
                self.executor.choose_timeline(action.timeline_card_id)
                return ActionExecutionResult(True, "已选择时间线按钮。")
            if (action.choice_count not in (1, 2, 3, 4)
                    or not 0 <= action.choice_index < action.choice_count):
                return self._reject("发现选项位置已经失效，未执行操作。")
            if action.choice_entity_id is not None and not self._choice_entity_ok(
                    action, state):
                return self._reject("发现选项已经变化，未执行操作。")
            if (action.target is not None
                    and not self._target_exists(action.target, state)):
                # 目标已经没了：连选项都别点，点了这只手就停在半途（游戏等你点
                # 目标、脚本以为做完了），会一直烧到回合结束。
                return self._reject("发现选项的目标已经不存在，未执行操作。")
            if action.target is not None:
                label = self._target_label(action.target)
                self.executor.choose_discover_card(
                    action.choice_index, action.choice_count,
                    self._target_for_click(action.target, state),
                    self._board_slot_count(state, "friendly"),
                    self._board_slot_count(state, "enemy"),
                    self._choice_target_delay())
                return ActionExecutionResult(
                    True,
                    f"已选择发现选项：{action.choice_index + 1}号位"
                    f"，{label}")
            self.executor.choose_discover_card(
                action.choice_index, action.choice_count)
            return ActionExecutionResult(
                True, f"已选择发现选项：{action.choice_index + 1}号位")

        if isinstance(action, UseAbilityAction):
            if not self._target_exists(action.source, state):
                return self._reject("发动技能的随从已经不存在，未执行操作。")
            minion = state.my_minions[action.source.index]
            if getattr(minion, "card_id", None) != action.card_id:
                return self._reject("发动技能的随从已经变化，未执行操作。")
            if (action.choice_count not in (1, 2, 3, 4)
                    or not 0 <= action.choice_index < action.choice_count):
                return self._reject("技能号位已经失效，未执行操作。")
            if (action.target is not None
                    and not self._target_exists(action.target, state)):
                # 目标没了就别点技能：点了这只手会停在"等选目标"，脚本却以为
                # 做完了，会一直烧到回合结束。
                return self._reject("技能目标已经不存在，未执行操作。")
            name = action.choice_name or "技能"
            detail = f"已发动技能：{name}（{action.choice_index + 1}号位）"
            if action.target is not None:
                detail += f"，{self._target_label(action.target)}"
            self.executor.use_ability(
                self._target_for_click(action.source, state),
                self._board_slot_count(state, "friendly"),
                action.choice_index, action.choice_count,
                self._target_for_click(action.target, state),
                self._board_slot_count(state, "enemy"),
                self._choice_target_delay(),
            )
            return ActionExecutionResult(True, detail)

        if isinstance(action, SelectTargetAction):
            if action.target is None or not self._target_exists(action.target, state):
                # 目标已经没了/换了人：一下都不点，交给外层重新读推荐。
                return self._reject("目标已经不存在，未执行选择。")
            label = self._target_label(action.target)
            self.executor.select_target(
                self._target_for_click(action.target, state),
                self._board_slot_count(state, "friendly"),
                self._board_slot_count(state, "enemy"),
                len(state.my_hand_cards),
            )
            return ActionExecutionResult(True, f"已选择目标：{label}")

        if isinstance(action, UseLocationAction):
            locations = getattr(state, "my_locations", [])
            if not 0 <= action.location_index < len(locations):
                return self._reject("地标位置已经失效，未执行操作。")
            location = locations[action.location_index]
            if location.card_id != action.card_id:
                return self._reject("地标内容已经变化，未执行操作。")
            if (action.location_entity_id is not None
                    and getattr(location, "entity_id", None)
                    != action.location_entity_id):
                return self._reject("地标对象已经变化，未执行操作。")
            if not self._target_exists(action.target, state):
                return self._reject("地标目标已经不存在，未执行操作。")
            location_detail = f"已使用地标：{location.name}"
            if action.target is not None and action.target.kind == "hand":
                # 地标让你选一张自己的手牌（守护巨龙之厅、维希度斯的窟穴…）：
                # 同样按卡面要求的类型校验一次，日志里记清楚点的是哪张。
                target_card = state.my_hand_cards[action.target.index]
                if hand_target_cardtype_mismatch(location.card_id,
                                                 target_card):
                    return self._reject(
                        "手牌目标类型不符合该卡要求，未执行操作。")
                location_detail += (
                    f"，选手中第 {action.target.index + 1} 号位"
                    f"（{getattr(target_card, 'name', '?')}）")
            zone_pos = getattr(location, "zone_pos", 0)
            if zone_pos <= 0:
                return self._reject("地标战场位置未知，未执行操作。")
            my_board_count = self._board_slot_count(state, "friendly")
            oppo_board_count = self._board_slot_count(state, "enemy")
            if not 1 <= zone_pos <= my_board_count:
                return self._reject("地标战场位置已经失效，未执行操作。")
            self.executor.use_location(
                zone_pos - 1,
                my_board_count,
                self._target_for_click(action.target, state),
                my_board_count,
                oppo_board_count,
                len(state.my_hand_cards),
            )
            return ActionExecutionResult(True, location_detail)

        if isinstance(action, LaunchStarshipAction):
            if not 0 <= action.starship_index < len(state.my_minions):
                return self._reject("星舰位置已经失效，未执行发射。")
            starship = state.my_minions[action.starship_index]
            if starship.card_id != action.card_id:
                return self._reject("星舰内容已经变化，未执行发射。")
            if (action.starship_entity_id is not None
                    and getattr(starship, "entity_id", None)
                    != action.starship_entity_id):
                return self._reject("星舰对象已经变化，未执行发射。")
            zone_pos = getattr(starship, "zone_pos", 0)
            board_count = self._board_slot_count(state, "friendly")
            if not 1 <= zone_pos <= board_count:
                return self._reject("星舰战场位置已经失效，未执行发射。")
            self.executor.launch_starship(zone_pos - 1, board_count)
            return ActionExecutionResult(True, "已发射星舰。")

        if isinstance(action, AttackAction):
            if not self._target_exists(action.attacker, state):
                return self._reject("攻击者已经不存在，未执行操作。")
            if not self._target_exists(action.target, state):
                return self._reject("攻击目标已经不存在，未执行操作。")

            self.executor.attack(
                self._target_for_click(action.attacker, state),
                self._target_for_click(action.target, state),
                self._board_slot_count(state, "friendly"),
                self._board_slot_count(state, "enemy"),
            )
            return ActionExecutionResult(True, "已执行攻击。")

        if isinstance(action, EndTurnAction):
            self.executor.end_turn()
            return ActionExecutionResult(True, "已请求结束回合。")

        if isinstance(action, TimelineAction):
            if action.choice not in ("undo", "keep"):
                return self._reject("未知的时间线操作，未执行。")
            self.executor.timeline_click(action.choice)
            return ActionExecutionResult(
                True, "已点击回溯。" if action.choice == "undo"
                else "已点击维持。")

        return self._reject("未知操作，未执行。")

    def run_turn_step(self, state, refresh_state) -> ActionExecutionResult:
        self.output(state.format_for_manual_control())
        action = self.prompt_turn_action(state)
        action = self.bind_to_turn(action, state)
        if isinstance(action, RefreshAction):
            refresh_state()
            return ActionExecutionResult(False, "已刷新战局状态。")

        fresh_state = refresh_state()
        if fresh_state is None:
            return self._reject("未能刷新日志状态，未执行操作。")
        return self.execute(action, fresh_state)

