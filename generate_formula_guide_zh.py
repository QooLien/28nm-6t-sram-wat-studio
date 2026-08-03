from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "tmp" / "matplotlib"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    Image,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


OUTPUT_DIR = ROOT / "output" / "pdf"
TEMP_DIR = ROOT / "tmp" / "pdfs" / "formula_guide_zh"
OUTPUT_PDF = OUTPUT_DIR / "HV28_SRAM_Analysis_Formula_Guide.pdf"

MSJH = Path(r"C:\Windows\Fonts\msjh.ttc")
MSJH_BOLD = Path(r"C:\Windows\Fonts\msjhbd.ttc")

NAVY = colors.HexColor("#12344D")
BLUE = colors.HexColor("#007AFF")
PALE_BLUE = colors.HexColor("#EAF3FF")
TEXT = colors.HexColor("#222222")
MUTED = colors.HexColor("#5E6877")
GRID = colors.HexColor("#CBD6E2")
NOTE_BG = colors.HexColor("#FFF6E4")
NOTE_BORDER = colors.HexColor("#E4A73A")


def register_fonts() -> None:
    if not MSJH.exists() or not MSJH_BOLD.exists():
        raise FileNotFoundError("Microsoft JhengHei fonts were not found in C:\\Windows\\Fonts")
    pdfmetrics.registerFont(TTFont("MSJH", str(MSJH), subfontIndex=0))
    pdfmetrics.registerFont(TTFont("MSJH-Bold", str(MSJH_BOLD), subfontIndex=0))
    pdfmetrics.registerFontFamily(
        "MSJH", normal="MSJH", bold="MSJH-Bold", italic="MSJH", boldItalic="MSJH-Bold"
    )


def math_png(name: str, expression: str, font_size: float = 11.0) -> Path:
    """Render one equation using STIX math typography at publication resolution."""
    path = TEMP_DIR / f"{name}.png"
    prop = FontProperties(family="STIXGeneral", size=font_size)
    fig = plt.figure(figsize=(1, 1), dpi=360)
    fig.patch.set_alpha(0)
    fig.text(0, 0, f"${expression}$", fontproperties=prop, color="#12344D")
    fig.savefig(path, dpi=360, transparent=True, bbox_inches="tight", pad_inches=0.025)
    plt.close(fig)
    return path


class Rule(Flowable):
    def __init__(self, width: float, color=GRID, thickness: float = 0.6):
        super().__init__()
        self.width = width
        self.height = 4
        self.color = color
        self.thickness = thickness

    def draw(self) -> None:
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.thickness)
        self.canv.line(0, 2, self.width, 2)


def formula_flowable(path: Path, max_width: float = 166 * mm, max_height: float = 21 * mm) -> Image:
    with PILImage.open(path) as im:
        px_w, px_h = im.size
    width = px_w / 360.0 * 72.0
    height = px_h / 360.0 * 72.0
    scale = min(1.0, max_width / width, max_height / height)
    image = Image(str(path), width=width * scale, height=height * scale)
    image.hAlign = "CENTER"
    return image


def build_styles():
    styles = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "TitleZH", parent=styles["Title"], fontName="MSJH-Bold", fontSize=22,
            leading=30, textColor=NAVY, alignment=TA_CENTER, spaceAfter=4 * mm,
        ),
        "subtitle": ParagraphStyle(
            "SubtitleZH", parent=styles["Normal"], fontName="MSJH", fontSize=10.5,
            leading=16, textColor=MUTED, alignment=TA_CENTER, spaceAfter=10 * mm,
        ),
        "h1": ParagraphStyle(
            "H1ZH", parent=styles["Heading1"], fontName="MSJH-Bold", fontSize=15,
            leading=22, textColor=BLUE, spaceBefore=4 * mm, spaceAfter=2.5 * mm,
            keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "H2ZH", parent=styles["Heading2"], fontName="MSJH-Bold", fontSize=12,
            leading=18, textColor=NAVY, spaceBefore=3 * mm, spaceAfter=1.5 * mm,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "BodyZH", parent=styles["BodyText"], fontName="MSJH", fontSize=10.5,
            leading=17, textColor=TEXT, alignment=TA_LEFT, spaceAfter=2.2 * mm,
            wordWrap="CJK",
        ),
        "small": ParagraphStyle(
            "SmallZH", parent=styles["BodyText"], fontName="MSJH", fontSize=8.7,
            leading=13.5, textColor=MUTED, wordWrap="CJK",
        ),
        "eq_label": ParagraphStyle(
            "EquationLabelZH", parent=styles["BodyText"], fontName="MSJH", fontSize=9,
            leading=13, textColor=MUTED, spaceBefore=1.5 * mm, spaceAfter=0.6 * mm,
            keepWithNext=True,
        ),
        "table_head": ParagraphStyle(
            "TableHeadZH", parent=styles["BodyText"], fontName="MSJH-Bold", fontSize=9.3,
            leading=14, textColor=NAVY, wordWrap="CJK",
        ),
        "table_body": ParagraphStyle(
            "TableBodyZH", parent=styles["BodyText"], fontName="MSJH", fontSize=9.1,
            leading=14.5, textColor=TEXT, wordWrap="CJK",
        ),
        "note": ParagraphStyle(
            "NoteZH", parent=styles["BodyText"], fontName="MSJH", fontSize=9.5,
            leading=15.5, textColor=colors.HexColor("#5D4600"), wordWrap="CJK",
        ),
    }


def eq(styles, number: str, title: str, image_path: Path):
    return KeepTogether([
        Paragraph(f"式 ({number})　{title}", styles["eq_label"]),
        formula_flowable(image_path),
        Spacer(1, 1.4 * mm),
    ])


def header_footer(canvas, doc) -> None:
    canvas.saveState()
    width, height = A4
    canvas.setStrokeColor(GRID)
    canvas.setLineWidth(0.5)
    canvas.line(18 * mm, 14 * mm, width - 18 * mm, 14 * mm)
    canvas.setFont("MSJH", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 9 * mm, "HV28 SRAM Analysis - WAT／RSNM／Write Margin 中文說明")
    canvas.drawRightString(width - 18 * mm, 9 * mm, f"第 {doc.page} 頁")
    canvas.restoreState()


def make_document() -> Path:
    register_fonts()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    styles = build_styles()

    equations = {
        "01": math_png("eq01", r"\beta=\frac{2I_{\mathrm{DSAT}}}{\left[\max\left(V_{\mathrm{WAT}}-\left|V_T\right|,\,0.05\right)\right]^2}"),
        "02": math_png("eq02", r"\mathrm{CR}_{\beta}=\frac{\beta_{\mathrm{PD}}}{\beta_{\mathrm{PG}}},\qquad \mathrm{PUR}_{\beta}=\frac{\beta_{\mathrm{PG}}}{\beta_{\mathrm{PU}}}"),
        "03": math_png("eq03", r"\mathrm{CR}_{I}=\frac{I_{\mathrm{DSAT,PD}}}{I_{\mathrm{DSAT,PG}}},\qquad \mathrm{PUR}_{I}=\frac{I_{\mathrm{DSAT,PG}}}{I_{\mathrm{DSAT,PU}}}"),
        "04": math_png("eq04", r"V_{\mathrm{OV}}=V_{\mathrm{GS}}-V_T"),
        "05a": math_png("eq05a", r"I_D=0,\qquad \left(V_{\mathrm{OV}}\leq0\ \mathrm{or}\ V_{\mathrm{DS}}\leq0\right)"),
        "05b": math_png("eq05b", r"I_D=\beta\left(V_{\mathrm{OV}}V_{\mathrm{DS}}-\frac{V_{\mathrm{DS}}^2}{2}\right),\qquad 0<V_{\mathrm{DS}}<V_{\mathrm{OV}}"),
        "05c": math_png("eq05c", r"I_D=\frac{1}{2}\beta V_{\mathrm{OV}}^2,\qquad V_{\mathrm{DS}}\geq V_{\mathrm{OV}}"),
        "06": math_png("eq06", r"I_{\mathrm{PU}}(V_{\mathrm{DD}}-V_{\mathrm{in}},\,V_{\mathrm{DD}}-V_{\mathrm{out}})+I_{\mathrm{ACC}}-I_{\mathrm{PD}}(V_{\mathrm{in}},\,V_{\mathrm{out}})=0"),
        "07a": math_png("eq07a", r"I_{\mathrm{ACC}}=I_{\mathrm{PG}}(V_{\mathrm{WL}}-Q,\,V_{\mathrm{BL}}-Q),\qquad V_{\mathrm{BL}}\geq Q"),
        "07b": math_png("eq07b", r"I_{\mathrm{ACC}}=-I_{\mathrm{PG}}(V_{\mathrm{WL}}-V_{\mathrm{BL}},\,Q-V_{\mathrm{BL}}),\qquad V_{\mathrm{BL}}<Q"),
        "08": math_png("eq08", r"s_i=\max\left\{s:\,[x,x+s]\times[y,y+s]\subseteq\mathcal{L}_i\right\},\qquad \mathrm{RSNM}_{\mathrm{geom}}=\min(s_1,s_2)"),
        "09": math_png("eq09", r"\mathrm{RSNM}_{\mathrm{geom}}(\mathrm{mV})=1000\,\mathrm{RSNM}_{\mathrm{geom}}(\mathrm{V})"),
        "10": math_png("eq10", r"q=\frac{\beta_{\mathrm{PU}}}{\beta_{\mathrm{PG}}},\qquad r=\frac{\beta_{\mathrm{PD}}}{\beta_{\mathrm{PG}}},\qquad V_{\mathrm{TH,eff}}=\frac{|V_{T,\mathrm{PU}}|+V_{T,\mathrm{PG}}+V_{T,\mathrm{PD}}}{3}"),
        "11": math_png("eq11", r"V_S=V_{\mathrm{DD}}-V_{\mathrm{TH,eff}},\qquad V_R=V_S-\frac{r}{r+1}V_{\mathrm{TH,eff}}"),
        "12": math_png("eq12", r"k=\frac{r}{r+1}\left[\sqrt{\frac{r+1}{(r+1)-V_S^2/V_R^2}}-1\right]"),
        "13a": math_png("eq13a", r"A=\frac{V_{\mathrm{DD}}-\frac{2r+1}{r+1}V_{\mathrm{TH,eff}}}{1+\frac{r}{k(r+1)}}"),
        "13b": math_png("eq13b", r"B=\frac{V_{\mathrm{DD}}-2V_{\mathrm{TH,eff}}}{1+\frac{kr}{q}+\sqrt{\frac{r}{q}\left(1+2k+\frac{r}{q}k^2\right)}}"),
        "14": math_png("eq14", r"\mathrm{RSNM}_{\mathrm{analytical}}=V_{\mathrm{TH,eff}}-\frac{A-B}{k+1}"),
        "15": math_png("eq15", r"V_{\mathrm{out,hold}}(V_{\mathrm{TRIP}})=V_{\mathrm{TRIP}}"),
        "16": math_png("eq16", r"I_{\mathrm{PU,trip}}=I_{\mathrm{PU}}(V_{\mathrm{DD}},\,V_{\mathrm{DD}}-V_{\mathrm{TRIP}}),\qquad I_{\mathrm{ACC,write}}=I_{\mathrm{PG}}(V_{\mathrm{WL}}-V_{\mathrm{BL}},\,V_{\mathrm{TRIP}}-V_{\mathrm{BL}})"),
        "17": math_png("eq17", r"\mathrm{WTM}=\max\left\{V_{\mathrm{BL}}-V_{\mathrm{BL,nom}}:\,V_{\mathrm{BL}}<V_{\mathrm{TRIP}},\ I_{\mathrm{ACC,write}}\geq I_{\mathrm{PU,trip}}\right\}"),
        "18": math_png("eq18", r"X(V)=X_1+\frac{V-V_1}{V_2-V_1}\left(X_2-X_1\right),\qquad X\in\left\{V_T,I_{\mathrm{DSAT}}\right\}"),
        "19": math_png("eq19", r"V_{\mathrm{DD,boundary}}:\quad \mathrm{WTM}\!\left(V_{\mathrm{DD,boundary}}\right)\rightarrow0^{+}"),
        "20": math_png("eq20", r"\mathrm{CR}_{W}=\frac{W_{\mathrm{PD}}}{W_{\mathrm{PG}}},\qquad \mathrm{PUR}_{W}=\frac{W_{\mathrm{PG}}}{W_{\mathrm{PU}}}"),
        "21": math_png("eq21", r"V_T[\mathrm{V}]=10^{-3}V_T[\mathrm{mV}],\qquad I_{\mathrm{DSAT}}[\mu\mathrm{A}]=10^3I[\mathrm{mA}]=10^6I[\mathrm{A}]=10^{-3}I[\mathrm{nA}]"),
    }

    frame = Frame(18 * mm, 18 * mm, A4[0] - 36 * mm, A4[1] - 31 * mm,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc = BaseDocTemplate(
        str(OUTPUT_PDF), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=13 * mm, bottomMargin=18 * mm,
        title="HV28 SRAM Analysis - WAT Vt／Idsat、Read SNM 與 Write Margin 中文說明",
        author="HV28 SRAM Analysis",
        subject="WAT Vt／Idsat 轉換、6T SRAM Read SNM 與 Write Margin Test 公式",
    )
    doc.addPageTemplates(PageTemplate(id="main", frames=[frame], onPage=header_footer))

    story = [
        Spacer(1, 7 * mm),
        Paragraph("HV28 SRAM Analysis", styles["title"]),
        Paragraph("WAT Vt／Idsat 轉換、Read SNM 與 Write Margin Test", styles["subtitle"]),
        Paragraph("文件目的與適用範圍", styles["h1"]),
        Paragraph(
            "本文件以繁體中文整理目前分析工具實際使用的計算流程。輸入資料以 WAT 量測之 PU、PG、PD 閾值電壓 Vt 與飽和電流 Idsat 為主，轉換為簡化驅動係數後建立 Read SNM 與 Write Margin 趨勢。此模型適合比較 Lot/Wafer、VDD 與 WAT Target，不等同 foundry PDK、BSIM model card、實測 WT Vmin 或量產 sign-off。",
            styles["body"],
        ),
    ]

    summary_data = [
        [Paragraph("輸出項目", styles["table_head"]), Paragraph("本工具採用的定義", styles["table_head"])],
        [Paragraph("幾何 Read SNM", styles["table_body"]), Paragraph("Read butterfly 左右兩個眼區各自可容納的最大正方形，取較小正方形的邊長作為主要 Read SNM。", styles["table_body"])],
        [Paragraph("解析式 RSNM", styles["table_body"]), Paragraph("依長通道平方律假設計算的獨立參考值，對應目前程式實作的式 (15)。", styles["table_body"])],
        [Paragraph("Write Margin Test", styles["table_body"]), Paragraph("在 PG 仍可克服 PU 的條件下，寫入低位元線可容許上升的最大電壓裕量；VDD sweep 再估計 margin 消失的邊界。", styles["table_body"])],
    ]
    summary = Table(summary_data, colWidths=[42 * mm, 124 * mm], repeatRows=1, hAlign="CENTER")
    summary.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PALE_BLUE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, GRID),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.extend([
        summary,
        Spacer(1, 3 * mm),
        Paragraph("符號與單位", styles["h2"]),
        Paragraph("電壓單位為 V，Idsat 單位為 μA，β 單位為 μA/V²。報告中的 Vin 與 Vout 均為實際電壓座標，而非 0 至 1 的正規化比例。", styles["body"]),
        eq(styles, "0", "常用 WAT 單位轉換", equations["21"]),
        Paragraph("Excel loader 亦支援 V／mV／μV／nV 與 A／mA／μA／nA／pA；所有資料先統一成 V 與 μA，再進入下列模型。", styles["small"]),
        Paragraph("1. WAT 校正與驅動能力比例", styles["h1"]),
        Paragraph("PU、PG、PD 分別由其 WAT Vt 與 Idsat 校正。為避免工作電壓接近 Vt 時分母趨近零，程式將有效 overdrive 的下限設為 0.05 V。", styles["body"]),
        eq(styles, "1", "WAT 校正後的驅動係數", equations["01"]),
        Paragraph("由 β 得到含 Vt 影響的驅動能力比；同時保留直接以 Idsat 計算的簡化比值，方便對照原始 WAT。PU、PG、PD 必須各自使用同一量測電壓與一致電流單位，PMOS Vt 在程式中取絕對值。", styles["body"]),
        eq(styles, "2", "β 驅動能力比例", equations["02"]),
        eq(styles, "3", "Idsat 直接比例", equations["03"]),
        Paragraph("目前介面中的 Channel Length 與 PU／PG／PD Width 用來建立幾何比例參考：", styles["body"]),
        eq(styles, "4", "6T 幾何 Cell Ratio 與 Pull-up Ratio", equations["20"]),
        Paragraph("重要：因輸入的 Idsat 是總電流 μA，已含量測元件的尺寸效應，所以目前不再將 W/L 乘入 β。只有取得 WAT test-key 的 W/L 或單位寬度電流 μA/μm 時，才適合進一步縮放至 SRAM cell 尺寸。", styles["note"]),
        Paragraph("2. 簡化平方律 MOS 電流模型", styles["h1"]),
        Paragraph("下列分段電流式分別套用於 PU、PG 與 PD。它保留 cutoff、linear 與 saturation 三種區域，但不包含短通道效應、DIBL、速度飽和、通道長度調變及溫度相關的完整 PDK 參數。", styles["body"]),
        eq(styles, "5", "有效 overdrive", equations["04"]),
        eq(styles, "6a", "截止區", equations["05a"]),
        eq(styles, "6b", "線性區", equations["05b"]),
        eq(styles, "6c", "飽和區", equations["05c"]),
        Paragraph("3. Read VTC 與幾何 Read SNM", styles["h1"]),
        Paragraph("對每一個 Vin，程式在 Vout 位於 0 到 VDD 的範圍內，以二分法求解儲存節點的電流平衡。Read 狀態下，PG 經由已預充電的 bitline 接至儲存節點，WL 與 BL 電壓由 Model Settings 的比例設定。", styles["body"]),
        eq(styles, "7", "反相器節點電流平衡", equations["06"]),
        Paragraph("IACC 定義為流入儲存節點 Q 的有號電流，因此 bitline 高於或低於 Q 時使用不同方向。", styles["body"]),
        eq(styles, "8a", "BL 高於或等於 Q 時的 access 電流", equations["07a"]),
        eq(styles, "8b", "BL < Q 時的 access 電流", equations["07b"]),
        PageBreak(),
        Paragraph("3.1 Butterfly 曲線與最大內接正方形", styles["h2"]),
        Paragraph("六顆 MOS 獨立輸入時，直接曲線由右側反相器建立，第二條曲線使用左側反相器 VTC 的數值反函數，因此 PUL／PGL／PDL 與 PUR／PGR／PDR mismatch 可形成不同大小的兩個眼區。程式以數值 trip point 分區，分別搜尋最大軸向正方形；最終 Read SNM 取兩個正方形邊長中的較小值。", styles["body"]),
        eq(styles, "9", "幾何 Read SNM", equations["08"]),
        eq(styles, "10", "伏特轉毫伏", equations["09"]),
        Paragraph("判讀重點：SNM 的工程數值是正方形邊長，不是正方形面積；邊長越大代表靜態抗雜訊能力越高，但不應直接解讀為 SRAM 的 Vmin。", styles["note"]),
        Paragraph("4. 解析式 Read SNM 參考", styles["h1"]),
        Paragraph("工具另提供長通道解析式作為獨立參考，基礎為使用者提供教材 High-Speed CMOS Circuit Technology 第 3.4.2 節之 Eq. 3.36。原式假設 PU、PG、PD 共用一個 threshold voltage；本工具將三組 WAT Vt 映射為算術平均的有效閾值。", styles["body"]),
        eq(styles, "11", "解析式參數定義", equations["10"]),
        eq(styles, "12", "中間電壓", equations["11"]),
        eq(styles, "13", "解析係數 k", equations["12"]),
        Paragraph("只有在所有分母非零、根號內為非負值，且結果位於 0 至 VDD 的物理範圍內時，程式才輸出解析式 RSNM。", styles["body"]),
        eq(styles, "14a", "解析項 A", equations["13a"]),
        eq(styles, "14b", "解析項 B", equations["13b"]),
        eq(styles, "15", "解析式 Read SNM", equations["14"]),
        PageBreak(),
        Paragraph("5. Write Margin Test", styles["h1"]),
        Paragraph("預設寫入條件為 WL = VDD、低位元線 BL = 0 V、高位元線 BLB = VDD，用於寫入 Q = 0。Write VTC 本來就不對稱，因此本工具不以 butterfly 內接正方形定義寫入能力，而是計算低位元線方向的 Write Trip Margin。", styles["body"]),
        eq(styles, "16", "Hold 狀態的 trip point", equations["15"]),
        eq(styles, "17", "寫入時 PU 與 PG 電流比較", equations["16"]),
        eq(styles, "18", "Write Trip Margin", equations["17"]),
        Paragraph("程式在 nominal low BL 與 VTRIP 之間用二分法尋找極限。數值越大，表示低位元線即使因雜訊而上升，PG 仍有較大的餘裕克服 PU；Margin 為 0 則代表這組簡化條件下 PG 無法建立正的寫入裕量。", styles["body"]),
        Paragraph("5.1 VDD Sweep 與寫入邊界", styles["h2"]),
        Paragraph("Write Margin 分頁對每一列 VDD，使用該列 PU／PG／PD 的 Vt 與 Idsat 重新校正 β。當相鄰兩列分別為 NO WRITE MARGIN 與 WRITABLE 時，程式先對 Vt、Idsat 做線性插值，再以二分法尋找邊界。", styles["body"]),
        eq(styles, "19", "相鄰 VDD 點的 WAT 參數插值", equations["18"]),
        eq(styles, "20", "預估 Write Margin 邊界", equations["19"]),
        Paragraph("此邊界是 cell-level compact-model 參考，不是 Select_Write Vmin。實際 WT 結果還包含 array、decoder、wordline／bitline RC、sense／write driver、測試條件與 weakest-bit 統計。", styles["note"]),
        Paragraph("6. 使用與判讀限制", styles["h1"]),
    ])

    limits = Table([
        [Paragraph("可用於", styles["table_head"]), Paragraph("不應單獨用於", styles["table_head"])],
        [Paragraph("同一組假設下比較 Lot/Wafer WAT 與 WAT Target", styles["table_body"]), Paragraph("晶圓廠 sign-off 或取代 PDK／BSIM", styles["table_body"])],
        [Paragraph("觀察 PU／PG／PD Vt、Idsat 與 Read／Write margin 的相對趨勢", styles["table_body"]), Paragraph("把 RSNM 或 Write Margin 邊界直接視為 Read／Write Vmin", styles["table_body"])],
        [Paragraph("建立合理的 28 nm 工程數據樣本與討論基準", styles["table_body"]), Paragraph("推論完整 24 Mb array 的 fail probability 或 yield", styles["table_body"])],
    ], colWidths=[83 * mm, 83 * mm], repeatRows=1)
    limits.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PALE_BLUE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, GRID),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.extend([
        limits,
        Spacer(1, 4 * mm),
        Table([[Paragraph("建議以完整 WT 的 Scan4N、Select_Write、Select_Read Vmin 建立統計相關性，再判斷 datasheet WAT Target 是否落在合理製程視窗內。", styles["note"])]],
              colWidths=[166 * mm], style=TableStyle([
                  ("BACKGROUND", (0, 0), (-1, -1), NOTE_BG),
                  ("BOX", (0, 0), (-1, -1), 0.8, NOTE_BORDER),
                  ("LEFTPADDING", (0, 0), (-1, -1), 10),
                  ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                  ("TOPPADDING", (0, 0), (-1, -1), 9),
                  ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
              ])),
        Spacer(1, 3 * mm),
        Paragraph("公式來源：解析式 Read SNM 參考 High-Speed CMOS Circuit Technology，第 3.4.2 節 Eq. 3.36。版本：2026-08-03。", styles["small"]),
    ])

    doc.build(story)
    return OUTPUT_PDF


if __name__ == "__main__":
    try:
        output = make_document()
        print(output)
    finally:
        shutil.rmtree(TEMP_DIR, ignore_errors=True)
