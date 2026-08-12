# -*- coding: utf-8 -*-
"""
builder.py — собирает готовую презентацию из шаблона.
Тебе НЕ нужно менять этот файл.

Поддерживает ДВА типа шаблонов автоматически:

1) "Разметочный" режим (как раньше) — template.pptx с 4 слайдами-образцами,
   где текст помечен метками {{TITLE}}, {{TEXT}} и т.д., а картинка —
   фигурой с именем IMAGE_PLACEHOLDER. Нужен для дизайнов со свободным
   декором (звёздочки, рисованные элементы и т.п.), где невозможно
   автоматически понять "что тут заголовок, а что — просто картинка".

2) "Автоматический" режим — для обычных PowerPoint-тем без ручной разметки.
   Определяется сам: если в первых слайдах шаблона НЕТ меток {{...}},
   бот использует встроенные в тему слайд-макеты (Title Slide,
   Title and Content, Picture with Caption и т.д.) и подставляет текст/
   картинку в их родные плейсхолдеры. Такой template.pptx можно просто
   скачать из интернета и положить как есть, без редактирования.
"""
import io
from pptx import Presentation
from pptx.util import Emu
from pptx.enum.shapes import PP_PLACEHOLDER_TYPE as PPT

LAYOUTS = {
    "title": 0,
    "text_image": 1,
    "bullets": 2,
    "final": 3,
}


# ==================================================================
#  РЕЖИМ 1: разметочный (метки {{...}} + IMAGE_PLACEHOLDER)
# ==================================================================
import copy


def _clone_slide(prs, source_slide):
    blank_layout = prs.slide_layouts[6]
    new_slide = prs.slides.add_slide(blank_layout)
    for shape in source_slide.shapes:
        el = copy.deepcopy(shape._element)
        new_slide.shapes._spTree.insert_element_before(el, "p:extLst")
    return new_slide


def _fill_slide_markers(slide, data: dict, image_bytes):
    for shape in list(slide.shapes):
        if shape.name == "IMAGE_PLACEHOLDER":
            if image_bytes:
                left, top, w, h = shape.left, shape.top, shape.width, shape.height
                shape._element.getparent().remove(shape._element)
                slide.shapes.add_picture(io.BytesIO(image_bytes), left, top, w, h)
            else:
                if shape.has_text_frame:
                    for p in shape.text_frame.paragraphs:
                        for r in p.runs:
                            r.text = ""
            continue
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    for key, value in data.items():
                        if isinstance(value, str):
                            run.text = run.text.replace("{{" + key + "}}", value)


def _has_markers(prs) -> bool:
    """Проверяет, использует ли шаблон разметку {{...}} на первых слайдах."""
    for slide in list(prs.slides)[:4]:
        for shape in slide.shapes:
            if shape.has_text_frame and "{{" in shape.text_frame.text:
                return True
    return False


def _build_marker_mode(prs, structure, images) -> bytes:
    template_slides = list(prs.slides)

    for i, slide_data in enumerate(structure["slides"]):
        layout_idx = LAYOUTS.get(slide_data.get("layout", "bullets"), 2)
        new_slide = _clone_slide(prs, template_slides[layout_idx])
        _fill_slide_markers(new_slide, slide_data, images.get(i))

    xml_slides = prs.slides._sldIdLst
    for _ in range(len(template_slides)):
        xml_slides.remove(list(xml_slides)[0])

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ==================================================================
#  РЕЖИМ 2: автоматический (родные слайд-макеты темы)
# ==================================================================

_HOUSEKEEPING = {PPT.DATE, PPT.FOOTER, PPT.SLIDE_NUMBER, PPT.HEADER}


def _pick_layout(prs, groups: list, avoid: set = frozenset()):
    """
    groups — список множеств типов, где из КАЖДОЙ группы нужен хотя бы один
    плейсхолдер в макете (например [{TITLE}, {BODY, OBJECT}]).
    Среди подходящих макетов выбирается САМЫЙ ПРОСТОЙ (меньше всего лишних
    полей) — чтобы не попасть на макет с 4 колонками ради списка из 3 пунктов.
    """
    candidates = []
    for layout in prs.slide_layouts:
        types = {ph.placeholder_format.type for ph in layout.placeholders}
        if types & avoid:
            continue
        matched = sum(1 for g in groups if types & g)
        if matched == 0:
            continue
        content_size = len(types - _HOUSEKEEPING)
        candidates.append((matched, content_size, layout))

    if not candidates:
        return prs.slide_layouts[0]

    candidates.sort(key=lambda t: (-t[0], t[1]))
    return candidates[0][2]


def _find_ph(slide, types: set):
    for ph in slide.placeholders:
        if ph.placeholder_format.type in types:
            return ph
    return None


def _build_native_slide(prs, slide_data: dict, image_bytes):
    kind = slide_data.get("layout", "bullets")

    if kind == "title":
        layout = _pick_layout(prs, [{PPT.CENTER_TITLE, PPT.TITLE}, {PPT.SUBTITLE}],
                               avoid={PPT.OBJECT, PPT.BODY, PPT.PICTURE})
    elif kind == "text_image":
        layout = _pick_layout(prs, [{PPT.TITLE, PPT.CENTER_TITLE}, {PPT.PICTURE}])
    elif kind == "final":
        layout = _pick_layout(prs, [{PPT.TITLE, PPT.CENTER_TITLE}],
                               avoid={PPT.OBJECT, PPT.BODY, PPT.PICTURE})
    else:  # bullets
        layout = _pick_layout(prs, [{PPT.TITLE, PPT.CENTER_TITLE}, {PPT.BODY, PPT.OBJECT}],
                               avoid={PPT.PICTURE})

    slide = prs.slides.add_slide(layout)

    title_ph = _find_ph(slide, {PPT.TITLE, PPT.CENTER_TITLE})
    if title_ph and slide_data.get("TITLE"):
        title_ph.text_frame.text = slide_data["TITLE"]

    sub_ph = _find_ph(slide, {PPT.SUBTITLE})
    if sub_ph and slide_data.get("SUBTITLE"):
        sub_ph.text_frame.text = slide_data["SUBTITLE"]

    body_ph = _find_ph(slide, {PPT.BODY, PPT.OBJECT})
    bullets = [v for k, v in sorted(slide_data.items()) if k.startswith("BULLET_") and v]
    if body_ph and bullets:
        tf = body_ph.text_frame
        tf.clear()
        for i, b in enumerate(bullets):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.text = b
    elif body_ph and slide_data.get("TEXT"):
        body_ph.text_frame.text = slide_data["TEXT"]

    pic_ph = _find_ph(slide, {PPT.PICTURE})
    if image_bytes:
        if pic_ph:
            pic_ph.insert_picture(io.BytesIO(image_bytes))
        else:
            # в макете нет спец-поля под фото — кладём картинку справа сами
            slide.shapes.add_picture(
                io.BytesIO(image_bytes),
                Emu(int(prs.slide_width * 0.55)),
                Emu(int(prs.slide_height * 0.15)),
                height=Emu(int(prs.slide_height * 0.7)),
            )


def _build_native_mode(prs, structure, images) -> bytes:
    original_count = len(list(prs.slides))

    for i, slide_data in enumerate(structure["slides"]):
        _build_native_slide(prs, slide_data, images.get(i))

    # убираем слайды, которые изначально были в скачанном файле (если были)
    xml_slides = prs.slides._sldIdLst
    for _ in range(original_count):
        xml_slides.remove(list(xml_slides)[0])

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ==================================================================
#  ТОЧКА ВХОДА
# ==================================================================

def build_pptx(template_path: str, structure: dict, images: dict) -> bytes:
    """
    template_path — путь к template.pptx
    structure — JSON от нейросети: {"slides": [{"layout": "title", "TITLE": "..."}, ...]}
    images — словарь {номер_слайда: байты_картинки}
    Возвращает готовый .pptx как байты.
    """
    prs = Presentation(template_path)

    if _has_markers(prs):
        return _build_marker_mode(prs, structure, images)
    else:
        return _build_native_mode(prs, structure, images)
