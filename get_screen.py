"""
主体代码引自 Demon_Hunter 的CSDN博客, 博客URL:https://blog.csdn.net/zhuisui_woxin/article/details/84345036
"""

import win32gui
import win32ui
import win32con
import win32com.client
import win32api
import win32process
import pywintypes
import numpy
from print_info import *

from constants.constants import *


HEARTHSTONE_BOX_TITLE_MARKERS = (
    "炉石传说盒子",
    "爐石戰記盒子",
)
HEARTHSTONE_BOX_EXECUTABLES = {"hsang.exe"}


def get_HS_hwnd():
    hwnd = win32gui.FindWindow(None, "炉石传说")
    if hwnd != 0:
        return hwnd

    hwnd = win32gui.FindWindow(None, "《爐石戰記》")
    if hwnd != 0:
        return hwnd

    hwnd = win32gui.FindWindow(None, "Hearthstone")
    return hwnd


def get_battlenet_hwnd():
    hwnd = win32gui.FindWindow(None, "战网")
    if hwnd != 0:
        return hwnd

    hwnd = win32gui.FindWindow(None, "Battle.net")
    return hwnd


def test_hs_available():
    return get_HS_hwnd() != 0


def get_window_process_name(hwnd):
    process_handle = None
    try:
        _, process_id = win32process.GetWindowThreadProcessId(hwnd)
        access = win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ
        process_handle = win32api.OpenProcess(access, False, process_id)
        executable_path = win32process.GetModuleFileNameEx(process_handle, 0)
        return executable_path.rsplit("\\", 1)[-1]
    except (OSError, pywintypes.error):
        return ""
    finally:
        if process_handle:
            win32api.CloseHandle(process_handle)


def is_hearthstone_box_window(
        hwnd, title_func=win32gui.GetWindowText,
        process_name_func=get_window_process_name):
    if not hwnd:
        return False
    try:
        title = title_func(hwnd) or ""
    except Exception:
        return False
    if any(marker in title for marker in HEARTHSTONE_BOX_TITLE_MARKERS):
        return True
    try:
        process_name = process_name_func(hwnd) or ""
    except Exception:
        return False
    return process_name.lower() in HEARTHSTONE_BOX_EXECUTABLES


def is_allowed_hearthstone_foreground(
        hearthstone_hwnd, foreground_hwnd=None,
        title_func=win32gui.GetWindowText,
        process_name_func=get_window_process_name):
    if foreground_hwnd is None:
        foreground_hwnd = win32gui.GetForegroundWindow()
    return (foreground_hwnd == hearthstone_hwnd
            or is_hearthstone_box_window(
                foreground_hwnd, title_func, process_name_func))


def is_hearthstone_input_ready(hearthstone_hwnd):
    return is_allowed_hearthstone_foreground(hearthstone_hwnd)


def point_targets_hearthstone(
        x, y, hearthstone_hwnd, window_from_point=win32gui.WindowFromPoint,
        root_window=None):
    if root_window is None:
        root_window = lambda hwnd: win32gui.GetAncestor(hwnd, win32con.GA_ROOT)
    try:
        hit_window = window_from_point((int(x), int(y)))
        return root_window(hit_window) == root_window(hearthstone_hwnd)
    except Exception:
        return False


def move_window_foreground(hwnd, name=""):
    try:
        win32gui.BringWindowToTop(hwnd)
        shell = win32com.client.Dispatch("WScript.Shell")
        shell.SendKeys('%')
        win32gui.SetForegroundWindow(hwnd)
        win32gui.ShowWindow(hwnd, win32con.SW_NORMAL)
    except Exception as e:
        if name != "":
            warn_print(f"Open {name}: {e}")
        else:
            warn_print(e)
        return False
    return win32gui.GetForegroundWindow() == hwnd


def focus_hearthstone_if_present() -> bool:
    """炉石窗口已出现时把它切到前台（没出现就什么都不做）。

    重启/拉起炉石后，游戏窗口常常在战网或其它窗口**底层**；此时脚本的点击
    会落到别的窗口上（表现就是「点不着」）。这个函数只聚焦，不点战网、
    不启动进程，适合在等待窗口/主界面的轮询里反复调用。
    """
    hwnd = get_HS_hwnd()
    if not hwnd:
        return False
    move_window_foreground(hwnd, "炉石传说")
    return True


def max_diff(img, pixel_list):
    ans = 0
    for pair in pixel_list:
        diff = abs(int(img[pair[0]][pair[1]][1]) -
                   int(img[pair[0]][pair[1]][0]))
        ans = max(ans, diff)
        # print(img[pair[0]][pair[1]])

    return ans


def _bitblt_region(region):
    """桌面 DC 子区域 BitBlt → (h, w, 4) ndarray；区域非法/失败返回 None。"""
    try:
        left, top, right, bottom = (int(v) for v in region)
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            return None
        hwin = win32gui.GetDesktopWindow()
        hwndDC = win32gui.GetWindowDC(hwin)
        mfcDC = win32ui.CreateDCFromHandle(hwndDC)
        saveDC = mfcDC.CreateCompatibleDC()
        saveBitMap = win32ui.CreateBitmap()
        saveBitMap.CreateCompatibleBitmap(mfcDC, width, height)
        saveDC.SelectObject(saveBitMap)
        saveDC.BitBlt((0, 0), (width, height), mfcDC, (left, top),
                      win32con.SRCCOPY)
        signedIntsArray = saveBitMap.GetBitmapBits(True)
        win32gui.DeleteObject(saveBitMap.GetHandle())
        saveDC.DeleteDC()
        mfcDC.DeleteDC()
        win32gui.ReleaseDC(hwin, hwndDC)
        frame = numpy.frombuffer(signedIntsArray, dtype='uint8')
        frame.shape = (height, width, 4)
        return frame
    except Exception:
        return None


def catch_screen(name=None, frame_grabber=None, region=None):
    """Capture the visible desktop at its real size.

    The optional grabber makes screenshot behavior testable without reading
    or changing the user's desktop.

    region（可选）：只抓这块**屏幕坐标**矩形，返回 (region_h, region_w, 4) 的
    ndarray —— 省掉整屏传输，也给"只关心炉石客户区"的调用方一个明确的坐标系。
    region 为 None 时抓整屏（旧行为）。

    **窗口移动的原子性**：抓一帧约 100ms，这期间窗口若被拖动，抓到的画面与
    几何就对不上。传 region 时走 `display_mapping.capture_region`，它会复测
    窗口是否又动过并在必要时重抓（见那里的说明）。
    """
    if frame_grabber is not None:
        return frame_grabber()
    # 第一个参数是类名，第二个参数是窗口名字
    # hwnd -> Handle to a Window !
    # 如果找不到对应名字的窗口，返回0
    if name is not None:
        hwnd = win32gui.FindWindow(None, name)
    else:
        hwnd = get_HS_hwnd()

    if hwnd == 0:
        return

    if region is not None:
        # 让跟踪器开始工作：窗口一动，下面的换算与重抓立刻跟上。
        try:
            import window_tracker
            window_tracker.ensure_started(hwnd)
        except Exception:
            pass
        try:
            from display_mapping import capture_region
            frame, _mapping = capture_region(
                hwnd, grabber=_bitblt_region)
            if frame is not None:
                return frame
        except Exception:
            pass
        # 原子抓取不可用时退回单次 BitBlt（行为与改造前一致）。
        return _bitblt_region(region)

    width = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
    height = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
    # 返回句柄窗口的设备环境，覆盖整个窗口，包括非客户区，标题栏，菜单，边框 DC device context
    hwin = win32gui.GetDesktopWindow()
    
    hwndDC = win32gui.GetWindowDC(hwin)
    # 创建设备描述表
    mfcDC = win32ui.CreateDCFromHandle(hwndDC)
    # 创建内存设备描述表
    saveDC = mfcDC.CreateCompatibleDC()
    # 创建位图对象准备保存图片
    saveBitMap = win32ui.CreateBitmap()
    # 为bitmap开辟存储空间
    saveBitMap.CreateCompatibleBitmap(mfcDC, width, height)
    # 将截图保存到saveBitMap中
    saveDC.SelectObject(saveBitMap)
    # 保存bitmap到内存设备描述表
    saveDC.BitBlt((0, 0), (width, height), mfcDC, (0, 0), win32con.SRCCOPY)

    signedIntsArray = saveBitMap.GetBitmapBits(True)

    # 内存释放
    win32gui.DeleteObject(saveBitMap.GetHandle())
    saveDC.DeleteDC()
    mfcDC.DeleteDC()
    win32gui.ReleaseDC(hwin, hwndDC)

    im_opencv = numpy.frombuffer(signedIntsArray, dtype='uint8')
    im_opencv.shape = (height, width, 4)

    return im_opencv


# get_state 的像素判定点：**客户区相对坐标**（基准 1920×1080）。
# 画在 region_overlay 上时同样按这个口径（见 screen_regions.STATE_PROBE_POINTS）。
STATE_PROBE_POINTS = ((1090, 1070), (705, 305), (860, 960))


def _frame_size(im_opencv):
    """(height, width)：兼容 ndarray 与嵌套 list（后者仅测试用）。"""
    try:
        height, width = im_opencv.shape[0], im_opencv.shape[1]
        return int(height), int(width)
    except AttributeError:
        return len(im_opencv), (len(im_opencv[0]) if len(im_opencv) else 0)


def _pixel(im_opencv, x, y):
    """安全取像素（越界返回 None），避免换算出错时抛 IndexError 打断主循环。"""
    try:
        height, width = _frame_size(im_opencv)
        if not (0 <= x < width and 0 <= y < height):
            return None
        pixel = im_opencv[y][x]
        return [int(v) for v in pixel[:3]]
    except Exception:
        return None


def probe_pixels(im_opencv, mapping):
    """把三个判定点换算成这张抓图里的索引位置。

    抓图是炉石客户区时，客户区相对坐标要按客户区尺寸等比换算后索引；拿不到
    客户区（mapping 为 None）或退回整屏抓图时，直接用屏幕坐标索引（旧行为）。
    """
    height, width = _frame_size(im_opencv)
    if mapping is None:
        return list(STATE_PROBE_POINTS)
    client_width, client_height = mapping.width or 1, mapping.height or 1
    scale_x, scale_y = width / client_width, height / client_height
    return [(int(px * scale_x), int(py * scale_y)) for px, py in STATE_PROBE_POINTS]


def get_state():
    hwnd = get_HS_hwnd()
    if hwnd == 0:
        return FSM_LEAVE_HS

    # 窗口位置实时跟踪：窗口一动，下面取的几何与抓图立刻跟上（钩子不可用时
    # 由 display_mapping 的轮询兜底）。
    try:
        import window_tracker
        window_tracker.ensure_started(hwnd)
    except Exception:
        pass

    from display_mapping import display_mapping
    mapping = display_mapping(hwnd)

    # 只抓炉石客户区：判定点按客户区尺寸换算后索引，不受整屏分辨率与窗口位置
    # 影响。拿不到客户区（炉石最小化等）时退回整屏 + 屏幕坐标（旧行为）。
    if mapping is not None:
        im_opencv = catch_screen(region=mapping.client_box)
    else:
        im_opencv = catch_screen()

    if im_opencv is None:
        return FSM_BATTLING

    main_point, alt_point, mulligan_point = probe_pixels(im_opencv, mapping)
    main_pixel = _pixel(im_opencv, *main_point)      # 主界面/选英雄/匹配判定点
    alt_pixel = _pixel(im_opencv, *alt_point)        # 主界面判定点（备用）
    mulligan_pixel = _pixel(im_opencv, *mulligan_point)  # 选牌界面判定点

    # print("当前定位点------------")
    # print(main_pixel)
    # 先y轴再z轴
    if main_pixel in ([23, 52, 105], [20, 51, 104]) or \
            alt_pixel == [21, 43, 95]:   # 万圣节主界面会变
        return FSM_MAIN_MENU
    if main_pixel == [8, 18, 24]:
        return FSM_CHOOSING_HERO
    if main_pixel == [17, 18, 19]:
        return FSM_MATCHING
    if mulligan_pixel == [71, 71, 71]:
        return FSM_CHOOSING_CARD

    # 界面识别（只认文字）兜底：只有上面那些像素判定**都没命中**时才多给一次机会，
    # 所以对已经能认的（选牌/匹配）是纯加法。需要它的原因很具体：那三个判定点全
    # 落在**背景**上（换皮肤就废，注释里就写着"万圣节主界面会变"），用户反馈的
    # 主界面截图实测三个点一个都不匹配 → 一直被当成"对战中"；而按钮上的字没变。
    # 识别不出来 / 任何异常 → 原样退回 FSM_BATTLING（行为与改造前一致）。
    ui = _identify_ui(im_opencv, mapping)
    if ui:
        if ui["state"] == "deck_select":
            return FSM_CHOOSING_HERO
        if ui["state"] == "main_menu":
            return FSM_MAIN_MENU

    return FSM_BATTLING


def _identify_ui(frame, mapping):
    """让 battle_entry 认一次界面；不可用/任何异常一律返回 None（退回像素判定）。"""
    try:
        import battle_entry
        return battle_entry.identify_from_capture(frame, mapping)
    except Exception:
        return None


# def image_hash(img):
#     img = Image.fromarray(img)
#     return imagehash.phash(img)
#
#
# def hash_diff(str1, str2):
#     return bin(int(str1, 16) ^ int(str2, 16))[2:].count("1")


def terminate_HS():
    hwnd = get_HS_hwnd()
    if hwnd == 0:
        return
    _, process_id = win32process.GetWindowThreadProcessId(hwnd)
    handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE, 0, process_id)
    win32api.TerminateProcess(handle, 0)
    win32api.CloseHandle(handle)
