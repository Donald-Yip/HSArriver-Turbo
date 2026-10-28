"""古神「选择一个技能」面板：盒子给的技能名 → 面板号位。

实测（2026-10-27 用户截图 + 工程 PaddleOCR 复核）：带「古神」词条的随从不能
普通攻击，点它会弹出「选择一个技能」面板（四张技能牌等距横排）。盒子把发动
技能写成和攻击**同一行**，只多出「选择卡牌 + 技能名」：

    操作2号位随从攻击 / 克苏恩，帝国之眼 / 选择卡牌 / 克苏恩的愤怒

    操作2号位随从攻击 / 克苏恩，帝国之眼 / 目标是对方2号位 / 导航员伊莉斯 /
    选择卡牌 / 克苏恩的凝视

盒子**不给号位**、只给技能名，所以号位只能按实测的面板顺序记在
``SKILL_PANEL_ORDER`` 里（游戏里面板从左到右 = 元组从前到后的顺序）。

新增带技能面板的卡（别的古神 / 泰坦）时：在这里补一行（卡 id → 面板顺序的
(技能名, 技能子卡 id)），并在 tests/test_choose_skill.py 里补一条号位断言。
表里查不到时上层一律**拒绝重试、一下都不点**，绝不按别的位置乱点。
"""

import json
import re
from functools import lru_cache
from pathlib import Path

# 卡牌 id -> 面板从左到右的技能：(技能名, 技能子卡 id)。
# 元组第 1 项 = 最左 = 1 号位。
SKILL_PANEL_ORDER = {
    # 克苏恩，帝国之眼（10 费 12/12 古神）：
    #   1 克苏恩的愤怒 / 2 克苏恩的领域 / 3 克苏恩的凝视 / 4 克苏恩的仆从
    # 凝视还有一张同名变异子卡 BE_013t6（吸血版），号位同样是 3。
    "BE_013": (
        ("克苏恩的愤怒", "BE_013t1"),
        ("克苏恩的领域", "BE_013t2"),
        ("克苏恩的凝视", "BE_013t3"),
        ("克苏恩的仆从", "BE_013t4"),
    ),
}

# 卡面第一行就是这个词条。只用来"认出这确实是带技能面板的随从"（报错/日志
# 更清楚）；真正决定能不能点的是上面的顺序表。
_OLD_GOD_KEYWORD = "<b>古神</b>"


def _cards():
    # Read the project's local metadata only; never download in the click path.
    with (Path(__file__).resolve().parents[2] / 'cards.json').open(
            encoding='utf8') as source:
        return json.load(source)


@lru_cache(maxsize=1)
def _card_texts():
    return {card['id']: (card.get('text') or '') for card in _cards()}


def card_text(card_id):
    """卡面文字；读不到时返回空串。"""
    if not card_id:
        return ""
    try:
        return _card_texts().get(card_id, "")
    except Exception:
        return ""


def is_old_god_card(card_id) -> bool:
    """卡面第一行是不是「古神」词条；卡表读不出来时保守返回 False。

    注意不能直接 `"古神" in 卡面`：`BE_703 德雷阿佳丝` 的亡语文本里也有
    「抽一张古神牌」，那是引用不是词条。词条一定在第一行开头。
    """
    text = card_text(card_id).strip()
    if not text:
        return False
    return text.splitlines()[0].strip().startswith(_OLD_GOD_KEYWORD)


def skill_panel(card_id):
    """面板从左到右的 ((技能名, 技能子卡 id), ...)；没有记录时返回 ()。"""
    if not card_id:
        return ()
    return SKILL_PANEL_ORDER.get(card_id, ())


def is_choose_skill_card(card_id) -> bool:
    """这张卡是不是「点随从 → 选一个技能」的卡（记录表里有，或卡面带词条）。"""
    return bool(skill_panel(card_id)) or is_old_god_card(card_id)


def skill_names(card_id):
    """面板从左到右的技能名；没有记录时返回 ()。"""
    return tuple(name for name, _ in skill_panel(card_id))


def skill_name_at(card_id, index):
    """0 基号位的技能名；越界/没有记录返回 None。"""
    panel = skill_panel(card_id)
    if index is None or not 0 <= index < len(panel):
        return None
    return panel[index][0]


def skill_index(card_id, name):
    """技能名 → 0 基面板号位；认不出或不唯一时返回 None（宁可不点也不猜）。

    号位只能靠名字反查：盒子只写技能名。判不出来就让上层拒绝重试，绝不当
    「大概就是第几个」去点——点错技能（例如把「愤怒 21 伤」点成「仆从召唤」）
    的代价比这一回合不动更大。
    """
    if not name:
        return None
    wanted = _normalize_name(name)
    if not wanted:
        return None
    names = skill_names(card_id)
    exact = [index for index, option in enumerate(names)
             if _normalize_name(option) == wanted]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None
    # OCR 常把技能名认错一两个字（克苏恩的愤怒 → 克苏恩的惯怒）：只容忍
    # 唯一命中，两个都像就不猜。
    near = [index for index, option in enumerate(names)
            if _names_close(wanted, _normalize_name(option))]
    return near[0] if len(near) == 1 else None


# 与适配层（抉择/发现那条路）同样的归一化口径。刻意各写一份而不是从
# recommendation_adapter 导入：那边会 import 本模块，反向导入会成环。
_FULLWIDTH_TO_HALFWIDTH = str.maketrans(
    "０１２３４５６７８９（）【】《》，。、；：？！“”‘’ＡＢＣＤＥＦＧ",
    "0123456789()[]<>,.、;:?!\"\"''ABCDEFG")
_NAME_NOISE = re.compile(
    r"[\s·・．.,，。！!？?、：:；;（）()\[\]【】「」『』<>《》\-—_/～~\"']")


def _normalize_name(value):
    """技能名归一化：去空白/中英标点/间隔号，全角转半角，统一小写。"""
    if not value:
        return ""
    text = str(value).translate(_FULLWIDTH_TO_HALFWIDTH)
    return _NAME_NOISE.sub("", text).lower()


def _names_close(left, right):
    """两个归一化名字是不是"差一两个字"（OCR 容错）。

    只在长度 ≥3 且编辑距离 ≤1 时算命中：要求长度是为了避免「克苏恩的愤怒」
    被「克苏恩」这种截断误命中。
    """
    if not left or not right or min(len(left), len(right)) < 3:
        return False
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    return _edit_distance_at_most_one(left, right)


def _edit_distance_at_most_one(left, right):
    """左右是否只差一个增删改（长度差 ≤1 时用）。"""
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) <= 1
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    index = 0
    skipped = False
    for char in longer:
        if index < len(shorter) and shorter[index] == char:
            index += 1
            continue
        if skipped:
            return False
        skipped = True
    return True
