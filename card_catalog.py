"""卡牌库查询（只读本地 cards.json，绝不联网）。

为什么单独一个模块：自动投降规则里用户写的是**中文卡名**，而日志里只有
cardId。这个模块负责两边互通：

    「裂魂者阿扎莉娜」 → {"JAIL_430"}
    "神秘访客"         → {"REV_246", "CORE_REV_246"}   （同名多版本，全部返回）

刻意**不走** `json_op.read_json()`：那个函数在 cards.json 缺失时会联网下载，
一旦没网就卡在主循环里。这里缺文件只报错，不下载。

三类调用方：
  * `surrender_rules` —— 保存配置时把卡名编译成 id 集合（只做一次）；
  * `web_ui` —— 网页输入框的中文自动补全（`search()`）；
  * `hsctl card search` —— 命令行查卡。
"""

import difflib
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parent
CARDS_PATH = ROOT / "cards.json"

# 中文职业名 → 炉石日志里的 CLASS 枚举值。
# 日志里的 tag=CLASS 给的是 MAGE / PRIEST 这种枚举，用户配置里更可能写中文。
CLASS_ZH_TO_ENUM = {
    "德鲁伊": "DRUID",
    "猎人": "HUNTER",
    "法师": "MAGE",
    "圣骑士": "PALADIN",
    "牧师": "PRIEST",
    "潜行者": "ROGUE",
    "萨满": "SHAMAN",
    "术士": "WARLOCK",
    "战士": "WARRIOR",
    "恶魔猎手": "DEMONHUNTER",
    "死亡骑士": "DEATHKNIGHT",
    "中立": "NEUTRAL",
}
CLASS_ENUM_TO_ZH = {value: key for key, value in CLASS_ZH_TO_ENUM.items()}

# 卡牌类型的中文说明（自动补全的候选行要显示「牧师随从」这种字样）。
TYPE_ZH = {
    "MINION": "随从",
    "SPELL": "法术",
    "WEAPON": "武器",
    "HERO": "英雄",
    "HERO_POWER": "英雄技能",
    "LOCATION": "地点",
    "BATTLEGROUND_SPELL": "酒馆法术",
}

# 模糊匹配的及格线。中文卡名差一个字时 difflib 的比值大约 0.7~0.8，
# 所以不能照搬英文的 0.85（会把「裂魂者阿莉扎娜」判成完全找不到）。
FUZZY_CUTOFF = 0.6
# 最佳候选要比第二名明显更像，才敢自动纠正；差距太小说明真有歧义。
FUZZY_MARGIN = 0.08


def normalize(text) -> str:
    """卡名规范化：全半角统一、去空白与标点、转小写。

    「神秘 访客」「神秘访客！」「Mystery　Visitor」都应该能对上同一个名字。
    """
    if text is None:
        return ""
    raw = unicodedata.normalize("NFKC", str(text)).lower()
    return "".join(ch for ch in raw if ch.isalnum())


def parse_class(value) -> Optional[str]:
    """把用户写的职业转成日志枚举：支持「牧师」/「PRIEST」/「priest」。

    认不出来返回 None（调用方据此报「职业写错了」，不要静默当不限）。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text in CLASS_ZH_TO_ENUM:
        return CLASS_ZH_TO_ENUM[text]
    upper = text.upper()
    if upper in CLASS_ENUM_TO_ZH:
        return upper
    # 少量常见别名，避免用户写「瞎子」「DK」时一头雾水。
    alias = {"瞎子": "DEMONHUNTER", "DH": "DEMONHUNTER",
             "DK": "DEATHKNIGHT", "死骑": "DEATHKNIGHT",
             "盗贼": "ROGUE", "骑士": "PALADIN", "萨满祭司": "SHAMAN"}
    return alias.get(upper) or alias.get(text)


def class_label(card_class) -> str:
    """枚举 → 中文，认不出就原样返回（网页候选行用）。"""
    if not card_class:
        return "中立"
    return CLASS_ENUM_TO_ZH.get(str(card_class).upper(), str(card_class))


# 酒馆战棋 / 乱斗 / 单人冒险的卡：永远不会出现在天梯对局里，但和真卡同名。
# 例：`神秘访客` 除了 REV_246 / CORE_REV_246，还有一张
# `Story_11_UnknownEnemy_012hb`（冒险模式同名卡）—— 合并进去会让界面上
# 显示个莫名其妙的 id，所以同名合并时优先只留"能进对局"的版本。
_NON_LADDER_ID_PREFIXES = ("BG", "TB_", "Story_", "GILA_BOSS", "BOTA_", "DALA_")
_NON_LADDER_SETS = {"BATTLEGROUNDS", "TB", "TAVERNS_OF_TIME"}


def is_ladder_playable(card: dict) -> bool:
    """这张卡可能出现在玩家对局里吗（排除酒馆/乱斗/冒险）。"""
    card_id = str(card.get("id") or "")
    if card_id.startswith(_NON_LADDER_ID_PREFIXES):
        return False
    return str(card.get("set") or "").upper() not in _NON_LADDER_SETS


@dataclass(frozen=True)
class CardHit:
    """一张卡（同名多版本合并成一条，`ids` 里是全版本）。"""

    name: str
    ids: tuple
    card_class: str = "NEUTRAL"
    card_type: str = ""
    cost: Optional[int] = None

    @property
    def primary_id(self) -> str:
        return self.ids[0] if self.ids else ""

    def describe(self) -> str:
        """候选行文案：「裂魂者阿扎莉娜 · 牧师随从 · 7费 · JAIL_430」。"""
        parts = [self.name, class_label(self.card_class) + TYPE_ZH.get(
            self.card_type, self.card_type or "")]
        if self.cost is not None:
            parts.append(f"{self.cost}费")
        parts.append(self.primary_id)
        if len(self.ids) > 1:
            parts[-1] = f"{self.primary_id}(+{len(self.ids) - 1})"
        return " · ".join(part for part in parts if part)

    def to_dict(self) -> dict:
        return {
            "id": self.primary_id,
            "ids": list(self.ids),
            "name": self.name,
            "class": self.card_class,
            "class_zh": class_label(self.card_class),
            "type": self.card_type,
            "type_zh": TYPE_ZH.get(self.card_type, ""),
            "cost": self.cost,
        }


@dataclass(frozen=True)
class CardLookup:
    """一次卡名查询的结果。

    `ok=False` 时 `error` 一定是给人看的中文原因，`suggestions` 给几个候选名
    方便保存时报错提示（网页把这些显示在对应输入框下面）。
    """

    ok: bool
    query: str
    hits: tuple = ()
    error: str = ""
    suggestions: tuple = ()
    # 模糊纠正时记录用户原本写的名字，便于提示「已把 X 纠正为 Y」。
    corrected_from: str = ""

    @property
    def card_ids(self) -> frozenset:
        ids = set()
        for hit in self.hits:
            ids.update(hit.ids)
        return frozenset(ids)

    @property
    def names(self) -> tuple:
        return tuple(hit.name for hit in self.hits)

    @property
    def name(self) -> str:
        return self.hits[0].name if self.hits else ""

    def warning(self) -> str:
        if not self.corrected_from:
            return ""
        return f"卡名「{self.corrected_from}」已自动纠正为「{self.name}」"


class CardCatalog:
    """cards.json 的内存索引。构造一次反复用（约 3.6 万条，加载 ~0.2 秒）。"""

    def __init__(self, cards: Iterable[dict]):
        self._by_id = {}
        self._by_name = {}
        # 保留原始顺序，保证同名的「主 id」稳定（先出现的当主 id）。
        for card in cards:
            card_id = str(card.get("id") or "").strip()
            if not card_id:
                continue
            self._by_id[card_id] = card
            key = normalize(card.get("name"))
            if key:
                self._by_name.setdefault(key, []).append(card)
        # 规范化名字 → 显示名（模糊匹配要拿它做 difflib 的候选池）
        self._display_names = {}
        for key, group in self._by_name.items():
            # 同名的"能进对局"版本排前面，主 id 就不会是酒馆/冒险那张。
            group.sort(key=lambda card: not is_ladder_playable(card))
            name = str(group[0].get("name") or "").strip()
            if name:
                self._display_names[key] = name

    # ------------------------------------------------------------ 构造
    @classmethod
    def from_file(cls, path: Optional[Path] = None) -> "CardCatalog":
        cards_path = Path(path) if path is not None else CARDS_PATH
        if not cards_path.exists():
            raise FileNotFoundError(
                f"找不到卡牌库 {cards_path}；自动投降/查卡需要它（不会联网下载）。")
        with cards_path.open("r", encoding="utf8") as handle:
            data = json.load(handle)
        if not isinstance(data, list):
            raise ValueError(f"{cards_path} 格式不对：期望一个卡牌数组。")
        return cls(data)

    # ------------------------------------------------------------ 索引访问
    def __len__(self) -> int:
        return len(self._by_id)

    def card(self, card_id) -> Optional[CardHit]:
        """按 cardId 取单张卡（不做同名合并，补全/展示用）。"""
        card = self._by_id.get(str(card_id) or "")
        return self._hit_for_group([card]) if card else None

    def class_of(self, card_id) -> str:
        """卡牌职业枚举；查不到返回空字符串（调用方自己决定怎么兜底）。"""
        card = self._by_id.get(str(card_id) or "")
        return str(card.get("cardClass") or "") if card else ""

    # ------------------------------------------------------------ 查询
    def search(self, text, limit: int = 8, card_type=None) -> list:
        """自动补全用：cardId 直接命中 / 名字**包含**匹配 / 打错字时模糊兜底。

        空输入返回空列表（不要一次吐 3.6 万条把网页卡死）。
        """
        raw = "" if text is None else str(text).strip()
        key = normalize(raw)
        if not key:
            return []
        limit = max(0, int(limit))
        # ① 用户可能直接粘 cardId（JAIL_430），别因为"不是中文名"就当没命中
        by_id = self._by_id.get(raw) or self._by_id.get(raw.upper())
        if by_id is not None:
            hit = self._hit_for_group([by_id])
            if hit is not None and (not card_type or hit.card_type == card_type):
                return [hit][:limit]
        exact, prefix, contains = [], [], []
        for norm_name, group in self._by_name.items():
            # 补全只给"能进对局"的卡：打「神秘」不该冒出一串酒馆战棋的卡。
            playable = [card for card in group if is_ladder_playable(card)]
            if not playable:
                continue
            if card_type:
                playable = [card for card in playable
                            if str(card.get("type") or "") == card_type]
                if not playable:
                    continue
            hit = self._hit_for_group(playable)
            if hit is None:
                continue
            if norm_name == key:
                exact.append(hit)
            elif norm_name.startswith(key):
                prefix.append(hit)
            elif key in norm_name:
                contains.append(hit)
        # 名字短的更像"用户想要的那张"，同组内按名字长度、再按 id 稳定排序。
        for bucket in (exact, prefix, contains):
            bucket.sort(key=lambda hit: (len(hit.name), hit.primary_id))
        results = exact + prefix + contains
        if not results and len(key) >= 2:
            # ② 打错字（阿莉扎娜 vs 阿扎莉娜）时不至于一个候选都不给
            results = self.fuzzy_suggest(key, limit=limit, card_type=card_type)
        return results[:limit]

    def fuzzy_suggest(self, text, limit: int = 8, card_type=None) -> list:
        """名字打错时的兜底候选（补全/表单提示用，不用于"精确判定"）。"""
        key = normalize(text)
        if not key:
            return []
        # 只在"长度接近"的候选里找，避免拿一个长名字硬凑。
        pool = [norm for norm in self._display_names
                if abs(len(norm) - len(key)) <= max(2, len(key) // 2)]
        matches = difflib.get_close_matches(key, pool, n=max(3, int(limit)),
                                           cutoff=FUZZY_CUTOFF)
        hits = []
        for norm in matches:
            group = [card for card in self._by_name.get(norm, ())
                     if is_ladder_playable(card)]
            if card_type:
                group = [card for card in group
                         if str(card.get("type") or "") == card_type]
            hit = self._hit_for_group(group)
            if hit is not None:
                hits.append(hit)
        return hits

    def lookup(self, query, allow_fuzzy: bool = True, card_type=None,
               aliases: Optional[dict] = None) -> CardLookup:
        """卡名或 cardId → 卡牌。多版本同名会一起返回。

        查找顺序（越靠前越可信）：
          1. 别名表（用户在配置里自己写的「阿莉扎娜 → 裂魂者阿扎莉娜」）
          2. cardId 精确
          3. 规范化名字精确
          4. 名字包含（唯一才算命中，多个 = 歧义）
          5. difflib 模糊（唯一且明显最像才自动纠正）
        """
        original = "" if query is None else str(query).strip()
        if not original:
            return CardLookup(False, original, error="卡牌不能为空")

        # ① 别名
        alias_target = ""
        if aliases:
            for alias, target in aliases.items():
                if normalize(alias) == normalize(original):
                    alias_target = str(target)
                    break
        if alias_target:
            return self.lookup(alias_target, allow_fuzzy=allow_fuzzy,
                               card_type=card_type, aliases=None)

        # ② cardId
        if original in self._by_id:
            hit = self._hit_for_group([self._by_id[original]])
            if card_type and (hit is None or hit.card_type != card_type):
                return CardLookup(
                    False, original,
                    error=f"「{original}」不是{card_type}类卡牌")
            return CardLookup(True, original, hits=(hit,))

        key = normalize(original)
        if not key:
            return CardLookup(False, original, error="卡牌名里没有可识别的字符")

        # ③ 名字精确
        if key in self._by_name:
            group = [card for card in self._by_name[key]
                     if not card_type or str(card.get("type") or "") == card_type]
            hit = self._hit_for_group(group) if group else None
            if hit is not None:
                return CardLookup(True, original, hits=(hit,))
            return CardLookup(
                False, original,
                error=f"「{original}」不是{card_type}类卡牌")

        # ④ 名字包含
        contains = [norm for norm in self._by_name if key in norm]
        if len(contains) == 1:
            group = [card for card in self._by_name[contains[0]]
                     if not card_type or str(card.get("type") or "") == card_type]
            hit = self._hit_for_group(group) if group else None
            if hit is not None:
                return CardLookup(True, original, hits=(hit,),
                                  corrected_from=original)
        if len(contains) > 1:
            names = tuple(self._display_names[norm] for norm in
                          sorted(contains, key=lambda n: (len(n), n))[:3])
            return CardLookup(
                False, original,
                error=f"「{original}」对上了 {len(contains)} 张卡，请写全名或 cardId",
                suggestions=names)

        # ⑤ 模糊
        if not allow_fuzzy:
            return CardLookup(
                False, original,
                error=f"找不到卡牌「{original}」",
                suggestions=self._suggest(key))
        # 只在"长度接近"的候选里找，避免拿一个长名字硬凑。
        pool = {norm: name for norm, name in self._display_names.items()
                if abs(len(norm) - len(key)) <= max(2, len(key) // 2)}
        matches = difflib.get_close_matches(key, list(pool), n=5,
                                           cutoff=FUZZY_CUTOFF)
        if not matches:
            return CardLookup(
                False, original,
                error=f"找不到卡牌「{original}」",
                suggestions=self._suggest(key))

        scored = []
        search_keys = list(pool)
        for norm in matches:
            score = difflib.SequenceMatcher(None, key, norm).ratio()
            scored.append((score, norm))
        scored.sort(reverse=True)
        best_score, best_norm = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        if len(scored) > 1 and best_score - second_score < FUZZY_MARGIN:
            names = tuple(pool[norm] for _score, norm in scored[:3])
            return CardLookup(
                False, original,
                error=(f"「{original}」可能对应多张卡，请写全名"),
                suggestions=names)
        group = self._by_name.get(best_norm, [])
        if card_type:
            group = [card for card in group
                     if str(card.get("type") or "") == card_type]
        hit = self._hit_for_group(group) if group else None
        if hit is None:
            return CardLookup(
                False, original,
                error=f"找不到卡牌「{original}」",
                suggestions=self._suggest(key))
        return CardLookup(True, original, hits=(hit,),
                          corrected_from=original)

    # ------------------------------------------------------------ 内部
    @staticmethod
    def _hit_for_group(group) -> Optional[CardHit]:
        if not group:
            return None
        # 同名多版本：优先只保留"能进对局"的版本（神秘访客的冒险态会被剔掉）；
        # 一个都没有说明用户查的就是酒馆/冒险卡，那就原样返回。
        playable = [card for card in group if is_ladder_playable(card)]
        group = playable or list(group)
        first = group[0]
        ids = []
        for card in group:
            card_id = str(card.get("id") or "")
            if card_id and card_id not in ids:
                ids.append(card_id)
        cost = first.get("cost")
        try:
            cost = int(cost) if cost is not None else None
        except (TypeError, ValueError):
            cost = None
        return CardHit(
            name=str(first.get("name") or "").strip(),
            ids=tuple(ids),
            card_class=str(first.get("cardClass") or "NEUTRAL"),
            card_type=str(first.get("type") or ""),
            cost=cost,
        )

    def _suggest(self, key, limit: int = 3) -> tuple:
        """找不到时给几个"长得像"的名字，别让用户对着报错干瞪眼。"""
        if not key:
            return ()
        matches = difflib.get_close_matches(
            key, list(self._display_names), n=limit, cutoff=0.45)
        return tuple(self._display_names[norm] for norm in matches)


_catalog: Optional[CardCatalog] = None
_catalog_error = ""


def default_catalog() -> Optional[CardCatalog]:
    """进程级单例。加载失败返回 None（调用方降级，不要抛异常打断主循环）。"""
    global _catalog, _catalog_error
    if _catalog is None and not _catalog_error:
        try:
            _catalog = CardCatalog.from_file()
        except Exception as exc:  # 缺文件/格式坏：记住原因，不反复重试
            _catalog_error = str(exc)
    return _catalog


def catalog_error() -> str:
    return _catalog_error


def reset_catalog_cache():
    """测试用：清掉单例，便于换一个临时卡库。"""
    global _catalog, _catalog_error
    _catalog = None
    _catalog_error = ""
