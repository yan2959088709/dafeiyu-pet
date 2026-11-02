# -*- coding: utf-8 -*-
"""移动 / 动画 / 绘制：tick 的三个子步骤、表情动作系统、气泡与说话、窗口绘制。

以 Mixin 形式挂到 PetWindow——方法体仍通过 self 访问主窗状态（皮肤、精灵帧、
气泡字段）；tick 本身留在主程序，因为它是跨域编排入口。

边界：_tick_consume_queues（对话投递）与 _tick_background_polls（后台取数）
分属别的域，各自留在原处。
"""
import math
import random
import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QApplication

from pet import ui_layout
from pet import action_binding

BUBBLE_H = 56
MARGIN = 4
SPEED = 380.0
TICK = 20

# 气泡排版：自适应字号、限行数、超长截断，保证气泡不越过桌宠头顶安全区（避免遮挡桌宠素材）
BUBBLE_MAX_LINES = 2
BUBBLE_MAX_H = 56.0          # 气泡最大高度(px)：顶部 by=6，底部≤62，桌宠头顶≈60
BUBBLE_MAX_TEXT_W = 240.0    # 气泡内文字最大宽度(px)

def layout_bubble_lines(base_font, text, max_w=BUBBLE_MAX_TEXT_W,
                        max_h=BUBBLE_MAX_H, max_lines=BUBBLE_MAX_LINES):
    """排版说话气泡文字，返回 (lines, font)。
    策略：优先 11pt；若超出 max_h 则逐级缩小字号；仍放不下则限行截断加省略号。"""
    def _wrap(fm, t, mw):
        ls, cur = [], ""
        for ch in t:
            if fm.horizontalAdvance(cur + ch) > mw:
                ls.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            ls.append(cur)
        return ls

    for size in (11.0, 10.5, 10.0, 9.5, 9.0):
        fnt = QFont(base_font)
        fnt.setPointSizeF(size)
        fm = QFontMetrics(fnt)
        full = _wrap(fm, text, max_w)
        lines = full[:max_lines]
        truncated = len(full) > max_lines
        if truncated and lines:
            last = lines[-1]
            # 末尾放不下省略号 → 压缩末行直到能放下
            while last and fm.horizontalAdvance(last + "…") > max_w:
                last = last[:-1]
            lines[-1] = last + "…"
        if len(lines) * fm.height() + 12 <= max_h:
            return lines, fnt
    # 极端情况（极小窗口/超长文本）：最小字号强行单行截断
    fnt = QFont(base_font)
    fnt.setPointSizeF(9.0)
    fm = QFontMetrics(fnt)
    line = text
    while line and fm.horizontalAdvance(line + "…") > max_w:
        line = line[:-1]
    return ([line + "…"] if line else ["…"]), fnt

# 睡觉/惊醒/醒来台词：只被 _enter_sleep / _wake_up 使用，随方法一起搬入本模块
STARTLE_LINES = [
    "呜哇！谁吵我！", "吓我一跳！做噩梦了都！", "唔……人家在睡觉的……",
    "突然被戳醒，气鼓鼓。", "zzz…哈？谁？！",
]
SLEEP_LINES = [
    "困了…我去眯一会儿 zzz", "好困，先睡为敬～", "电量不足，进入休眠…",
]
WAKE_LINES = [
    "呼啊～睡饱了", "伸个懒腰～精神多了！", "一觉醒来，又是搬砖的一天。",
]


class MotionMixin:
    """PetWindow 的移动/动画/绘制能力。"""

    # ---------- tick 子步骤 ----------
    def _tick_spring(self):
        """Q弹弹簧（squash & stretch）。"""
        if abs(self.squash - 1.0) > 0.002 or abs(self.squash_v) > 0.002:
            self.squash_v += (1.0 - self.squash) * 0.30
            self.squash_v *= 0.70
            self.squash += self.squash_v
            self.squash = max(0.40, min(1.50, self.squash))

    def _do_squash(self, s=0.62):
        """触发 Q 弹挤压（弹簧回弹动画）"""
        self.squash = s
        self.squash_v = 0.0

    def _tick_anim_clocks(self):
        """动作/表情/跳动/转向/冷却的计时器推进。"""
        if self._pending_emote:
            name, dur, loops = self._pending_emote
            self._pending_emote = None
            if not self._sleeping:      # 睡着时不拿后台事件把宠物叫醒
                self._start_emote(name, dur, loops)
        if self.emote == "sleep":
            if self.t * TICK >= self.sleep_until:
                self._wake_up(natural=True)
        elif self.dragging:
            pass   # 拖拽是最高优先级交互：冻结当前动作，松手后接着播
        elif self.emote_t > 0:
            self.emote_t -= TICK / 1000.0
            if self.emote_t <= 0:
                self._end_emote()
        if self._idle_cd > 0:
            self._idle_cd -= 1
        if getattr(self, "_turn_cd", 0) > 0:
            self._turn_cd -= 1
        if self._hover_cd > 0:
            self._hover_cd -= 1
        if self._tooltip_dirty:
            self._tooltip_dirty = False
            self._update_tray_tooltip()
        if self.jump_t > 0:
            self.jump_t = max(0.0, self.jump_t - 0.06)
        if self.cross_t > 0:
            self.cross_t = max(0.0, self.cross_t - 0.15)
        if self.action_t > 0:
            self.action_t = max(0.0, self.action_t - 0.03)
            if self.action_t == 0:
                self.action = None

    def _tick_movement(self):
        """三种模式的移动调度：跟随鼠标 / 自由散步 / 原地待着。"""
        if self.emote:
            return   # 动作播放期间暂停位移（走/跟都是最低优先级）
        if self.ds_busy:
            # 思考态显示的不是行走动画：不产生位移，旧的走位也作废
            if getattr(self, "_walk", None) is not None:
                self._stop_walk()
            return
        now_ms = self.t * TICK
        if now_ms < getattr(self, "rest_until", 0):
            return   # 动作结束后的 3–5s 静默：跟随/漫游都不位移
        if self.mode != "wander" and getattr(self, "_walk", None) is not None:
            self._stop_walk()            # 离开漫游模式即丢弃走位，避免下次跳帧
        if self.mode == "follow":
            cursor = self.cursor().pos()
            screen = QApplication.screenAt(cursor) or self.screen() or QApplication.primaryScreen()
            geo = screen.availableGeometry()
            near = (self.x() - 100 <= cursor.x() <= self.x() + self.width() + 100 and
                    self.y() - 100 <= cursor.y() <= self.y() + self.height() + 100)
            if near:
                if self.target is not None:
                    self.target = None
                    self._stop_walk()
            else:
                tx = max(geo.left(), min(geo.right() - self.width(), cursor.x() - self.width() / 2))
                ty = max(geo.top(), min(geo.bottom() - self.height(), cursor.y() - 90))
                if self.target is None and hasattr(self.skin, "pick_move"):
                    try:
                        self.skin.pick_move(self)
                    except Exception:
                        pass
                self.target = (tx, ty)
        elif self.mode == "wander":
            if getattr(self, "_walk", None) is None:
                if self.emote:
                    return   # 专属演出（转向/随机动作）期间不规划走位
                if now_ms < self.rest_until:
                    self._maybe_idle_action()
                    return
                self._plan_walk()
                if getattr(self, "_walk", None) is None:
                    return
            self._advance_walk(now_ms)
            return
        else:
            self._maybe_idle_action()
            return

        if self.target is not None:
            cx, cy = self.x() + self.width() / 2, self.y() + self.height() / 2
            dx, dy = self.target[0] - cx, self.target[1] - cy
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < 12:
                self.target = None
                self._stop_walk()
                self.rest_until = self.t * TICK + random.randint(8000, 18000)
            else:
                step = self.cur_speed * TICK / 1000.0
                nx, ny = cx + dx / dist * step, cy + dy / dist * step
                self.move(int(nx - self.width() / 2), int(ny - self.height() / 2))
                if abs(dx) > abs(dy) * 1.15:
                    direction = "left" if dx < 0 else "right"
                    # 跟随与自由行走共用同一套「方向 → 镜像」规则（旧皮肤保持原约定）
                    self._set_dir(direction, self._facing_for(direction, legacy_left=1))
                else:
                    self._set_dir("up" if dy < 0 else "down")
            if random.random() < 0.002 and self.jump_t == 0:
                self.jump_t = 0.5
        target_speed = SPEED if self.target is not None else 0.0
        self.cur_speed += (target_speed - self.cur_speed) * 0.3

    def _plan_walk(self):
        """漫游：按上游 moves 参数规划一次水平走位（lead/tail 缓入缓出）。

        专属行走动作（如 dsh-pet）按动作自身的 distance/margin/lead/tail 与
        素材 clip 推导步速；某侧被屏幕边缘夹得太短就换另一侧，避免「原地
        踏步」。没有动作素材的皮肤仍沿用旧的「距离 / SPEED」时长。
        """
        from pet import motion_engine
        geo = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        area = (geo.left(), geo.top(), geo.right(), geo.bottom())
        choice = None
        picker = getattr(self.skin, "pick_move", None)
        if callable(picker):
            try:
                choice = picker(self)
            except Exception:
                choice = None
        clip = 0.0
        if choice:
            default = choice.get("params") or {}
            moves = {"default": default}
            clip = float(choice.get("clip") or 0.0)
        else:
            moves = (self.cfg.get("animations") or {}).get("moves") or {}
            default = moves.get("default") or {}
        # 用户把「走动」绑定到某段动作时，演出时长以那段素材为准
        bound = action_binding.action_for(self.skin.name, "walk")
        if bound and hasattr(self.skin, "anim_duration"):
            clip = float(self.skin.anim_duration(bound) or clip)
        profile = (motion_engine.walk_profile(default, clip,
                                              self.pet_size or self.win_w)
                   if clip > 0 else None)
        half = self.width() / 2.0
        cx = self.x() + half
        if profile:
            # 先选动作，再在放大后的区间里抽距离（短→漂浮 / 中→螃蟹 / 长→奔跑）
            distance = random.uniform(profile["min"], profile["max"])
        else:
            distance = motion_engine.wander_distance(self.pet_size or self.win_w, moves)
        margin = float(default.get("margin", 20))
        left = area[0] + margin + half
        right = area[2] - margin - half
        direction = random.choice((-1, 1))
        min_step = motion_engine.MIN_WALK_PX
        if profile:
            min_step = max(min_step, profile["min"] * 0.5)
        plan = None
        for dirn in (direction, -direction):
            target_cx = motion_engine.plan_move(cx, dirn, distance,
                                               self.pet_size or self.win_w,
                                               margin, left, right)
            end_x = int(target_cx - half)
            actual = abs(end_x - self.x())
            if actual >= min_step:
                plan = (dirn, end_x, actual)
                break
        if plan is None:
            return
        dirn, end_x, actual = plan
        lead = float(default.get("leadSec", 2))
        tail = float(default.get("tailSec", 2))
        coast_sec = 0.0
        coaster = getattr(self.skin, "coast_after", None)
        if profile and choice and callable(coaster):
            try:
                coast_sec = max(0.0, float(coaster(choice.get("name")) or 0.0))
            except Exception:
                coast_sec = 0.0
        if profile:
            lead, tail = profile["lead"], profile["tail"]
            # 行走状态只活到「位移结束」：lead + 行进 + 惯性；
            # 不再把素材尾部（2/4.8s 站立）也算进走位，
            # 否则位移停了、跑步/踏步动画还在原地继续。
            walk_tail = coast_sec
            duration = lead + profile["travel"] + coast_sec
            self._walk_speed = actual / max(0.1, profile["travel"])
        else:
            walk_tail = tail
            duration = max(1.0, actual / SPEED) + lead + tail
            self._walk_speed = actual / max(0.1, duration - lead - tail)
        duration = max(duration, lead + walk_tail + 0.2)
        name = "left" if dirn < 0 else "right"
        # 先摆朝向（此刻还没进入 _walk，prev_key 仍是待机帧，交叉淡入才有效），
        # 走位启动不走“转向动画”分支：行走本身就是专属动作
        self._set_dir(name, self._facing_for(name), allow_turn=False)
        self._walk = motion_engine.Walk((self.x(), self.y()), (end_x, self.y()),
                                        duration, lead, walk_tail)
        self._walk_t0 = self.t * TICK
        self._walk_distance = actual
        # 动画从「真正开走」那一刻起从第 0 帧播，避免起跑/摔倒先于位移
        self._walk_anim_t0 = (self.frame_t + lead) if profile else 0.0
        room = ((right - half) - end_x) if dirn > 0 else (end_x - (left - half))
        self._walk_coast_sec = coast_sec
        self._walk_coast_dir = dirn
        self._walk_coast_dist = min(max(0.0, self._walk_speed * coast_sec / 2.0),
                                    max(0.0, room))
        self._walk_left, self._walk_right = left - half, right - half
        self._alog("walk start action=%s dir=%s dist=%d dur=%.1f speed=%.1f"
                   % (choice.get("name") if choice else "legacy", name, actual,
                      duration, self._walk_speed))

    def _facing_for(self, direction, legacy_left=-1):
        """方向 → 镜像符号：皮肤可覆写；旧皮肤保持原约定。"""
        mapper = getattr(self.skin, "facing_for_dir", None)
        if callable(mapper):
            try:
                return int(mapper(direction))
            except Exception:
                pass
        if direction == "left":
            return legacy_left
        return 1

    def _stop_walk(self, reset_facing=True):
        """终止走位：清动作名/惯性/朝向，并把状态键收回到静止态。"""
        self._walk = None
        clear = getattr(self.skin, "clear_move", None)
        if callable(clear):
            try:
                clear()
            except Exception:
                pass
        self._walk_coast_sec = 0.0
        self._walk_coast_dist = 0.0
        self._walk_anim_t0 = 0.0
        self._alog("walk stop")
        if reset_facing:
            self.dir = "down"
            self.facing = self._facing_for("down")

    def _advance_walk(self, now_ms):
        walk = getattr(self, "_walk", None)
        if walk is None:
            return
        elapsed = (now_ms - self._walk_t0) / 1000.0
        if elapsed > walk.duration + 1.0:
            # 陈旧走位（跨场景/长时间没 tick）：丢弃重规划，而不是一步跳到终点
            self._stop_walk()
            self._plan_walk()
            walk = getattr(self, "_walk", None)
            if walk is None:
                return
            elapsed = (now_ms - self._walk_t0) / 1000.0
        x, y, done = walk.at(elapsed)
        coast_sec = getattr(self, "_walk_coast_sec", 0.0)
        if coast_sec and elapsed > walk.lead + walk.travel:
            # 长跑结尾的摔跤：按 0.5s 线性减速补一小段惯性，再刹停
            dt = min(coast_sec, elapsed - (walk.lead + walk.travel))
            frac = 1.0 - (1.0 - dt / coast_sec) ** 2
            x += getattr(self, "_walk_coast_dir", 0) * \
                getattr(self, "_walk_coast_dist", 0.0) * frac
        x = max(getattr(self, "_walk_left", x), min(x, getattr(self, "_walk_right", x)))
        self.move(int(x), int(y))
        if random.random() < 0.002 and self.jump_t == 0:
            self.jump_t = 0.5
        if done:
            self._stop_walk()
            if self._emote_queue:
                self.rest_until = now_ms + random.randint(3000, 5000)
            else:
                self.rest_until = now_ms + random.randint(8000, 18000)

    # ---------- 鲸鱼娘表情/动作系统 ----------
    def _pet_state(self):
        if self.dragging:
            return "drag"
        if self.emote:
            return self.emote
        if self.ds_busy:
            return "think"
        if (self.target is not None or getattr(self, "_walk", None) is not None) \
                and not self.dragging:
            start = getattr(self, "_walk_anim_t0", 0.0)
            if start and self.frame_t + 1e-6 < start:
                return "idle"      # 起步前不播走位动画，等真正开走再入状态
            return "walk"
        return "idle"

    def _sprite_key(self):
        # 状态→帧映射由皮肤模块实现（每套皮肤独立规则），主程序只透传；
        # 动作绑定层先做一次名字覆盖（未配置就原样透传，行为不变）
        state = self._pet_state()
        self._render_state = state
        mapped = action_binding.action_for(self.skin.name, state) or state
        return self.skin.state_key(mapped, self.dir, self.facing, self.cur_h)

    def _set_dir(self, d, facing=None, allow_turn=True):
        if d != self.dir:
            self.prev_key = self._sprite_key()
            self.cross_t = 1.0
            self.dir = d
            if allow_turn:
                self._maybe_turn_anim(d)
        if facing is not None and facing != self.facing:
            self.facing = facing

    def _maybe_turn_anim(self, new_dir):
        """闲置时换向播一次专属转向动画（漫游/跟随/拖拽/已有动作时跳过）。"""
        if self.dragging or self._sleeping or self.emote:
            return
        if getattr(self, "_walk", None) is not None or self.target is not None:
            return
        if new_dir == "down" or getattr(self, "_turn_cd", 0) > 0:
            return
        turn = getattr(self.skin, "turn_anim", None)
        if not callable(turn):
            return
        name = turn()
        if not name:
            return
        dur = (self.skin.anim_duration(name)
               if hasattr(self.skin, "anim_duration") else 1.5)
        self._start_emote(name, dur=dur, loops=1, prio="random")
        self._turn_cd = int(60000 / TICK)

    def _maybe_idle_action(self):
        if self._idle_cd > 0:
            return
        if self.emote or self._sleeping:
            return
        # 重要气泡(如AI回复)正在展示时，不做随机待机动作，避免打断气泡（缺陷2）
        now = self.t * TICK / 1000.0
        if (self._dsh_bubble_until > 0 and self._dsh_bubble_until > time.time()
                and self._dsh_prio >= 1):
            return
        if self.skin.kind == "gadget":
            return   # 吸附挂件不走随机待机动作
        if self.skin.name == "whale-girl":
            # 长时间无人互动 → 自动去睡觉
            if (self.t - self._last_interact_t) > 11000 and random.random() < 0.03:
                self._enter_sleep()
                return
            if random.random() < 0.01:   # 降低待机动作触发频率
                self._alog("idle trigger skin=%s" % self.skin.name)
                if not self._play_action_binding("random"):
                    self.skin.idle_pick(self)
                self._idle_cd = random.randint(600, 1200)   # 冷却拉长：12s~24s
        else:
            # 鱼等普通皮肤：保留原有小动作
            if random.random() < 0.002:
                self._alog("idle trigger skin=%s" % self.skin.name)
                if not self._play_action_binding("random"):
                    self.skin.idle_pick(self)
                # 不论是否真的选出动作，都要上冷却，避免每帧重试造成连播
                self._idle_cd = random.randint(3600, 6000)

    def _play_action_binding(self, state, dur=6.0):
        """把某个交互事件绑定的固定动作播出来；没配置就返回 False 交给皮肤默认逻辑。"""
        name = action_binding.action_for(self.skin.name, state)
        if not name:
            return False
        self._alog("binding state=%s name=%s" % (state, name))
        self._start_emote(name, dur=dur, loops=1)
        return True

    def _alog(self, msg):
        """动作调度日志（logs.txt）：记录触发/排队/抢占/结束，方便排查连播。"""
        try:
            from pet import log
            log.info("ACTION t=%.1fs %s" % (self.t * TICK / 1000.0, msg))
        except Exception:
            pass

    def _start_emote(self, name, dur=2.0, loops=1, say=None, prio=None,
                     bypass_gate=False, source=None, replace_name=None):
        """触发一个表情/动作（name 须对应 WG_STATES）；loops 保证至少播放 N 个完整精灵循环。

        prio 见 action_binding：点击级 > 手动级 > 随机级 > 行走级。
        禁止打断规则：随机级永不断开行走/正在播的动作，统一排队；
        手动级/点击级仍可抢占（用户主动操作优先响应）。
        全局间隔：每段动作结束后随机级至少等 15–30s 再开下一段。
        返回 True 表示已经播上，False 表示进了队列。
        """
        value = (action_binding.PRIO_RANDOM if prio is None
                 else action_binding.priority_value(prio))
        now = self.t * TICK
        if self.dragging and value < action_binding.PRIO_CLICK:
            # 拖拽是最高优先级：拖拽期间的其它动作先排队
            self._queue_emote(name, dur, loops, say, value)
            self._alog("block drag name=%s prio=%s" % (name, value))
            return False
        if not bypass_gate and self.emote is None:
            gate = max(getattr(self, "_auto_next_at", 0),
                       getattr(self, "rest_until", 0))
            if now < gate:
                # 动作刚结束的 3–5s 静默期：任何新请求都排队（包括点击/手动）
                self._queue_emote(name, dur, loops, say, value)
                self._alog("block rest name=%s wait=%.1fs"
                           % (name, max(0.0, (gate - now) / 1000.0)))
                return False
        if value <= action_binding.PRIO_RANDOM and not bypass_gate:
            if self._walk is not None:
                # 自由行走进行中：随机级不打断脚步，等走完再播
                self._queue_emote(name, dur, loops, say, value)
                self._alog("block walk name=%s prio=%s" % (name, value))
                return False
            gate = max(getattr(self, "_auto_next_at", 0),
                       getattr(self, "rest_until", 0))
            if self.emote is None and now < gate:
                self._queue_emote(name, dur, loops, say, value)
                self._alog("block gap name=%s wait=%.1fs"
                           % (name, max(0.0, (gate - now) / 1000.0)))
                return False
        if self.emote and value < self._emote_prio:
            self._queue_emote(name, dur, loops, say, value)
            self._alog("block prio name=%s prio=%s cur=%s"
                       % (name, value, self._emote_prio))
            return False
        current_work = bool(
            self.emote and self.emote == getattr(self, "_work_anim", None))
        if self.emote and not current_work and self.emote != replace_name:
            # 演出动作未播完：任何新请求都只排队，不截断当前动作
            self._queue_emote(name, dur, loops, say, value)
            self._alog("block active name=%s cur=%s remain=%.1fs"
                       % (name, self.emote, max(0.0, self.emote_t)))
            return False
        if (self.emote and value == self._emote_prio
                and value <= action_binding.PRIO_RANDOM and source != "work"
                and not name.startswith(("余额-", "碎碎念-", "工作状态-"))):
            # 同为随机级也不互相打断；余额/碎碎念/工作状态需要即时切换，例外
            self._queue_emote(name, dur, loops, say, value)
            self._alog("block same name=%s" % name)
            return False
        if self._walk is not None and not self.dragging:
            # 手动/点击打断走位：先停走，结束后给 2–4s 喘息再重新规划
            self._post_action_rest_ms = random.randint(2000, 4000)
            self._stop_walk()
        prev = self.emote
        if getattr(self.skin, "single_shot", False):
            # 动作库皮肤：整段素材播完，不用调用方的 1.6~2.8s 拍子截断
            loops = 1
            getter = getattr(self.skin, "anim_duration", None)
            clip = None
            if callable(getter):
                try:
                    clip = float(getter(name) or 0.0)
                except Exception:
                    clip = None
            dur = clip if clip and clip > 0 else max(8.0, min(float(dur), 12.0))
        elif loops > 1 and (name, self.cur_h) in self.wg_frames:
            fps = self.skin.fps(name)
            cyc = max(0.2, len(self.wg_frames[(name, self.cur_h)]) / fps)
            dur = max(dur, cyc * loops)
        self.emote = name
        self.emote_t = dur
        self.emote_total = dur
        self.emote_loops = max(1, loops)
        self.emote_t0 = self.frame_t   # 区间循环/播一次要从动作开头算帧号
        self._emote_prio = value
        self._alog("start name=%s prio=%s dur=%.1f prev=%s"
                   % (name, value, dur, prev or "-"))
        if say:
            self.say(say)
        return True

    def _queue_emote(self, name, dur, loops, say, value):
        """低优先级动作排队：只留优先级最高的一个，同级保留先到的，避免风暴。"""
        if self._emote_queue and self._emote_queue[0][4] >= value:
            self._alog("queue drop name=%s prio=%s held=%s"
                       % (name, value, self._emote_queue[0][0]))
            return
        self._emote_queue = [(name, dur, loops, say, value, self.t * TICK)]
        self._alog("queue set name=%s prio=%s" % (name, value))

    def _tick_action_queue(self):
        """动作间隔闸门：前一段结束 15–30s 后才播放缓存的动作。"""
        q = self._emote_queue
        if not q:
            return
        now = self.t * TICK
        item = q[0]
        name = item[0]
        queued_at = item[5] if len(item) > 5 else now
        if now - queued_at > 60000:
            self._emote_queue = []
            self._alog("queue expire name=%s age=%.1fs"
                       % (name, (now - queued_at) / 1000.0))
            return
        if self.emote or self.dragging or self._sleeping or self._walk is not None:
            return
        if now < max(getattr(self, "_auto_next_at", 0),
                      getattr(self, "rest_until", 0)):
            return
        dur, loops, say, value = item[1], item[2], item[3], item[4]
        self._emote_queue = []
        self._alog("queue start name=%s wait=%.1fs"
                   % (name, (now - queued_at) / 1000.0))
        self._start_emote(name, dur=dur, loops=loops, say=say, prio=value,
                          bypass_gate=True)

    def _drag_interrupt(self):
        """拖拽开始：清掉待播，并让低于点击级的演出立即让位。"""
        self._emote_queue = []
        if self.emote and getattr(self, "_emote_prio", 0) < action_binding.PRIO_CLICK:
            self._end_emote()

    def _end_emote(self):
        was = self.emote
        remaining = max(0.0, self.emote_t)
        self.emote = None
        self.emote_t = 0.0
        self._emote_prio = 0
        if was and remaining > 0.4:
            self._alog("end early name=%s remain=%.1fs" % (was, remaining))
        if was == "wake":
            self._sleeping = False
        checker = getattr(self.skin, "is_turn_anim", None)
        if was and callable(checker):
            try:
                if checker(was):
                    # 上游约定：转向动画播完翻到另一侧
                    self.facing = -self.facing
            except Exception:
                pass
        now = self.t * TICK
        gap = random.randint(3000, 5000)
        self._auto_next_at = now + gap
        rest = getattr(self, "_post_action_rest_ms", 0)
        wait = max(gap, rest or 0)
        if not self.dragging and not self._sleeping:
            # 动作结束后必须有 3–5s 静默：走位与队列释放都被它挡住
            self._post_action_rest_ms = 0
            self.rest_until = now + wait
        work_anim = getattr(self, "_work_anim", None)
        if work_anim and not self.dragging and not self._sleeping:
            # 长驻工作动画不立刻接回，否则会形成“动作接动作”；先进队列等间隔
            self._post_action_rest_ms = 0
            if self._emote_queue and self._emote_queue[0][4] == action_binding.PRIO_IDLE:
                self._emote_queue = []     # 旧的工作待播被新状态顶掉
            self._queue_emote(work_anim, 3600.0, 1, None,
                              action_binding.PRIO_IDLE)
        self._alog("end name=%s queue=%d gap=%.0fs"
                   % (was or "-", len(self._emote_queue), gap / 1000.0))

    def _play_event_anim(self, event, index=None):
        """事件动画（余额/碎碎念）：配置里写了且皮肤有这段素材才播。"""
        from pet import animation_pool
        try:
            pool = animation_pool.AnimationPool(self.cfg.get("animations") or {})
            name = pool.event(event, index)
        except Exception:
            return None
        if not name:
            return None
        try:
            if not self._skin_has_state(name):
                return None
        except Exception:
            return None
        self._alog("event %s name=%s" % (event, name))
        self._start_emote(name, dur=8.0, loops=1, prio="random")
        return name

    def _enter_sleep(self):
        if self._sleeping:
            return
        self._sleeping = True
        self.emote = "sleep"
        self.emote_t = 0.0
        self.emote_t0 = self.frame_t
        self._emote_prio = action_binding.PRIO_IDLE
        self._emote_queue = []      # 睡前丢掉待播动作，醒来不该再演旧戏
        self._stop_walk()
        self.sleep_until = self.t * TICK + random.randint(120000, 300000)  # 2~5 分钟
        self.say(random.choice(SLEEP_LINES))

    def _wake_up(self, natural=False):
        self._sleeping = False
        self._last_interact_t = self.t  # 醒来后给一段安静期，避免立刻又睡
        self.emote = "wake"
        self.emote_t = 1.2
        self.emote_t0 = self.frame_t
        self._emote_prio = action_binding.PRIO_CLICK
        self._emote_queue = []
        self.say(random.choice(WAKE_LINES) if natural else random.choice(STARTLE_LINES))

    def _startle(self):
        """睡觉被吵醒 → 受惊（wake 表情 + 弹一下 + 惊叫）"""
        if not (self._sleeping or self.emote == "sleep"):
            return
        self._play_boing()
        self._wake_up(natural=False)

    def _maybe_startle(self):
        if self._sleeping or self.emote == "sleep":
            self._startle()
            return True
        return False

    # ---------- 气泡与说话 ----------
    def _bubble_tick(self):
        """官方气泡状态机（所有皮肤通用）：淡出切换 → 淡入新内容 / 超时自动收起。"""
        now = time.time()
        # 随机台词淡出 → 淡入新内容（切换不延长总时长）
        if self._dsh_bubble_switching:
            self._dsh_bubble_alpha = max(0.0, self._dsh_bubble_alpha - 0.25)
            if self._dsh_bubble_alpha <= 0.0:
                self._dsh_bubble_switching = False
                self._dsh_bubble_kind = self._dsh_pending_kind
                self._dsh_pending_kind = None
                self._dsh_bubble_alpha = 0.0
        # 气泡总显示时长到期自动收起
        if self._dsh_bubble_until > 0:
            if now >= self._dsh_bubble_until:
                self._dsh_bubble_until = 0.0
                self._dsh_bubble_kind = None
                self._dsh_bubble_alpha = 0.0
                self._dsh_bubble_switching = False
                self._dsh_pending_kind = None
                self._dsh_line_pick = None
                self._dsh_custom_text = None
                self._dsh_prio = 0
            elif not self._dsh_bubble_switching:
                self._dsh_bubble_alpha = min(1.0, self._dsh_bubble_alpha + 0.15)

    def _queue_say(self, text):
        """后台线程调用：只入队，由主线程 tick 统一弹出显示（线程安全）"""
        self._say_queue.append(text)

    def say(self, text, inner=False, dur=2.8, prio=0):
        # 全局统一：所有皮肤台词都走官方 DSH 风格气泡（内白椭圆），不再绘制旧式圆角气泡
        self._dsh_show_text(text, dur=dur, prio=prio)

    # ---------- 绘制 ----------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 全窗口透明清屏：根除 Windows 透明分层窗口的旧帧像素残留（残影叠加）
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        now = self.t * TICK / 1000.0

        # ---- 常驻余额徽标（已废弃：余额统一走官方气泡，此旧徽标不再绘制）----
        # ---- 桌宠本体（含 Q弹 squash & stretch）----
        cx = self.width() / 2
        walking = self.target is not None and not self.dragging
        if walking:
            sway = math.sin(now * 6.5) * 3.0     # 摆动放缓，减少“抽搐感”
            bob = -abs(math.sin(now * 3.5)) * 6.0
        else:
            sway = math.sin(now * 2.5) * 1.5
            bob = 0.0
        breath = 1.0 + 0.02 * math.sin(now * 2.5)
        scale = breath
        jump = -abs(math.sin(self.jump_t * 3.14159)) * 14 * self.jump_t if self.jump_t > 0 else 0
        act_rot = act_sx = act_sy = 0.0
        if self.action == "sway":
            act_rot = math.sin(self.action_t * 3.14159 * 2) * 10 * self.action_t
        elif self.action == "stretch":
            act_sy = 0.06 * math.sin(self.action_t * 3.14159)
            act_sx = -0.03 * math.sin(self.action_t * 3.14159)

        if self.skin.kind == "gadget" and self.skin.pressed:
            sy, sx = 0.88, 1.05      # 按压 Q 弹：scaleY(0.88) scaleX(1.05)，底部坐标不变
        else:
            sy = self.squash
            sx = 1.0 + max(-0.35, (1.0 - sy) * 0.75)

        def draw_one(key, opacity, live=True):
            """live=False 用于淡出中的旧帧：它不再参与动作绑定，避免换态瞬间套错区间。"""
            if key is None:
                return
            state = getattr(self, "_render_state", None) if live else None
            if len(key) == 2:
                frames = self.wg_frames.get(key) or self.wg_frames.get(("idle", key[1]))
                if not frames:
                    return
                fps = self.skin.fps(key[0])
                if state:
                    fps = action_binding.fps_for(self.skin.name, state) or fps
                if key[0] == "idle":
                    pix = frames[self.skin.pick_idle_frame(self, frames)]
                else:
                    # 帧钟：emote 从动作起点算（区间循环/播一次才有意义），
                    # 走位从「真正开走」的帧起算，静止起步阶段不抢跑动画；
                    # 其余长驻状态（拖拽等）沿用全局时钟
                    if self.emote:
                        t0 = getattr(self, "emote_t0", 0.0)
                    elif getattr(self, "_render_state", None) == "walk":
                        t0 = getattr(self, "_walk_anim_t0", 0.0)
                    else:
                        t0 = 0.0
                    cfg = action_binding.event_cfg(self.skin.name, state) if state else {}
                    idx = action_binding.frame_index(cfg, len(frames), fps,
                                                     self.frame_t - t0)
                    pix = frames[idx]
                # 素材基准朝向由皮肤声明：鲸鱼娘基准朝左（向右走时水平镜像）；
                # deepseek-girl 已分左右两套帧集（mirror_follows_facing=False）
                # 不镜像；动作绑定可以在此基础上再指定规则；皮肤可以用
                # mirror_for 排除带文字的动作（镜像会颠倒字）。
                if state:
                    default_mirror = self.skin.mirror_follows_facing
                    hook = getattr(self.skin, "mirror_for", None)
                    if callable(hook):
                        try:
                            default_mirror = bool(hook(key[0]))
                        except Exception:
                            pass
                    facing = action_binding.frame_facing(
                        self.skin.name, state, self.facing, default_mirror)
                else:
                    facing = self.facing if self.skin.mirror_follows_facing else 1
            elif len(key) == 3:
                name, h, facing = key
                pix = self.sprites.get((name, h))
                if pix is None:
                    return
            else:
                return
            ph = pix.height() * scale * (1 + act_sy) * sy
            pw = pix.width() * scale * (1 + act_sx) * sx
            dx = cx - pw / 2
            bottom = self._dsh_top_h() + ui_layout.load()["window"]["margin"] + self.cur_h
            dy = bottom - ph + jump + bob
            p.save()
            p.setOpacity(opacity)
            p.translate(cx, bottom)
            p.rotate(sway + act_rot)
            p.translate(-cx, -bottom)
            if facing < 0:
                p.translate(cx, 0)
                p.scale(-1, 1)
                p.translate(-cx, 0)
            p.drawPixmap(QRectF(dx, dy, pw, ph), pix,
                         QRectF(0, 0, pix.width(), pix.height()))
            p.restore()

        cur_key = self._sprite_key()
        if self.cross_t > 0:
            draw_one(self.prev_key, self.cross_t, live=False)
            draw_one(cur_key, 1.0 - self.cross_t)
        else:
            draw_one(cur_key, 1.0)

        # ---- DSH 小鲸鱼挂件气泡（顶层绘制：官方同款 z-index，气泡罩住素材头部）----
        if (self._dsh_bubble_until > 0
                and self._dsh_bubble_alpha > 0.01):
            self._dsh_draw_bubble(p)
