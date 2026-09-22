"""Render the main run-control paths without introducing runtime dependencies."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from matplotlib import pyplot
from matplotlib.font_manager import FontProperties, fontManager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from matplotlib.path import Path as DrawPath


def render() -> None:
    font_path = Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf")
    families = ["DejaVu Sans"]
    if font_path.exists():
        fontManager.addfont(str(font_path))
        families.append(FontProperties(fname=str(font_path)).get_name())
    font = FontProperties(family=families)
    figure, axes = pyplot.subplots(figsize=(14, 8))
    figure.set_facecolor("#f8fafc")
    axes.set(xlim=(0, 14), ylim=(-0.7, 9.8))
    axes.axis("off")

    def text(horizontal, vertical, value, size=11, color="#334155"):
        axes.text(horizontal, vertical, value, fontproperties=font, fontsize=size,
                  color=color, ha="center", va="center", zorder=4,
                  bbox={"facecolor": "#f8fafc", "edgecolor": "none", "pad": 2})

    def node(horizontal, vertical, title, state, fill="#e0f2fe", dashed=False):
        axes.add_patch(FancyBboxPatch(
            (horizontal - 1.12, vertical - 0.49), 2.24, 0.98,
            boxstyle="round,pad=0.08,rounding_size=0.14",
            facecolor=fill, edgecolor="#64748b", linewidth=1.3,
            linestyle="--" if dashed else "-", zorder=3,
        ))
        axes.text(horizontal, vertical + 0.14, title, fontproperties=font, fontsize=14,
                  color="#0f172a", ha="center", va="center", zorder=4)
        axes.text(horizontal, vertical - 0.22, state, fontsize=9,
                  color="#475569", ha="center", va="center", zorder=4)

    def arrow(start, end, *, curve=0, dashed=False, color="#64748b"):
        axes.add_patch(FancyArrowPatch(
            start, end, connectionstyle=f"arc3,rad={curve}", arrowstyle="-|>",
            mutation_scale=15, linewidth=1.5, color=color,
            linestyle="--" if dashed else "-", zorder=2,
        ))

    def route(points, color="#64748b"):
        axes.add_patch(FancyArrowPatch(
            path=DrawPath(points, [DrawPath.MOVETO] + [DrawPath.LINETO] * (len(points) - 1)),
            arrowstyle="-|>", mutation_scale=15, linewidth=1.5, color=color, zorder=2,
        ))

    text(7, 9.35, "运行状态机 · 主要流转", 22, "#0f172a")
    text(7, 8.85, "八种状态：控制故障可升级，人工处理后刷新复查，不自动续跑", 12)
    node(1.4, 7, "待启动", "idle", "#f1f5f9")
    node(4.6, 7, "运行中", "running")
    node(8.4, 7, "重试中", "retrying")
    node(12.4, 7, "已完成", "done", "#dcfce7")
    node(1.4, 4, "安全停止中", "stopping", "#fef3c7")
    node(4.6, 4, "已中止 / 已暂停", "aborted", "#fef3c7")
    node(8.4, 4, "等待用户决策", "awaiting_confirmation", "#fef3c7")
    node(12.4, 4, "转人工处理", "escalated", "#fee2e2")
    node(8.4, 0.75, "独立子工程", "retrying → running", "#ede9fe", dashed=True)
    arrow((2.6, 7), (3.4, 7))
    arrow((5.8, 7.15), (7.2, 7.15), curve=-0.2)
    text(6.5, 7.6, "受限自动重试", 10)
    arrow((7.2, 6.85), (5.8, 6.85), curve=-0.2)
    route([(4.6, 7.6), (4.6, 8.15), (12.4, 8.15), (12.4, 7.6)])
    text(10.7, 8.15, "流程完成", 10)
    arrow((3.7, 6.5), (1.9, 4.6))
    text(1.8, 5.75, "手动暂停", 10)
    arrow((2.6, 4), (3.4, 4))
    text(3, 4.65, "安全退出", 10)
    arrow((5.2, 6.45), (7.8, 4.6))
    text(5.65, 5.45, "报错待决策", 10)
    arrow((8.4, 4.6), (8.4, 6.4))
    text(8.4, 5.55, "原参数续跑", 10)
    arrow((5.8, 4), (7.2, 4))
    text(6.5, 4.5, "/resume", 10)
    arrow((9.25, 6.5), (11.6, 4.6))
    text(11.6, 5.7, "无法自动处理", 10)
    route([(12.4, 3.4), (12.4, 2.5), (4.6, 2.5), (4.6, 3.4)])
    text(10.8, 2.5, "处理完成＋刷新复查", 10)
    route([(1.4, 3.4), (1.4, 1.95), (13.8, 1.95), (13.8, 4), (13.6, 4)], color="#dc2626")
    text(3.5, 1.95, "停止失败／超时", 10, "#dc2626")
    arrow((8.4, 3.4), (8.4, 1.4), dashed=True, color="#7c3aed")
    text(6.95, 1.65, "确认最新方案", 10, "#7c3aed")
    text(10.75, 0.75, "父工程不变", 11, "#7c3aed")
    text(8.4, 3.15, "修改建议：仍留在等待态", 10)
    text(7, -0.28, "红线：停止故障升级　紫色虚线：创建分支（不是父工程迁移）", 10)
    text(7, -0.63, "其余执行/控制异常也可升级；复查须确认进程退出。done 不回退，恢复仍需哈希准入。", 9)
    output = Path(__file__).resolve().parents[1] / "docs" / "images"
    output.mkdir(parents=True, exist_ok=True)
    svg_path = output / "run_state_machine.svg"
    figure.savefig(svg_path, dpi=160, bbox_inches="tight")
    svg_path.write_text(
        "\n".join(line.rstrip() for line in svg_path.read_text(encoding="utf-8").splitlines()) + "\n",
        encoding="utf-8",
    )
    figure.savefig(output / "run_state_machine.png", dpi=160, bbox_inches="tight")
    pyplot.close(figure)


if __name__ == "__main__":
    render()
