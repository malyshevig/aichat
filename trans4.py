import streamlit as st
import fitz  # PyMuPDF
import openai
from typing import List, Dict, Tuple
import time
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

api_key = "no-key"
timeout = 1240

url = "http://localhost:8000/v1"
client = openai.OpenAI(
    base_url=url,  # адрес vLLM сервера
    api_key="no-key",  # любой непустой строки, если не задан --api-key
    timeout=timeout
)
model = client.models.list().data[0]
model_name = model.model_extra['root']
max_model_len = model.model_extra["max_model_len"]

# ============================================================
# Настройка страницы
# ============================================================
st.set_page_config(page_title="PDF Переводчик с сохранением дизайна", page_icon="📄", layout="wide")

# ============================================================
# Инициализация сессионного состояния
# ============================================================
if "original_pdf_bytes" not in st.session_state:
    st.session_state.original_pdf_bytes = None
if "translated_pdf_bytes" not in st.session_state:
    st.session_state.translated_pdf_bytes = None
if "text_blocks" not in st.session_state:
    st.session_state.text_blocks = None

st.title("📄 PDF Переводчик с сохранением дизайна")

# ============================================================
# Боковая панель
# ============================================================
with st.sidebar:
    st.header("⚙️ Настройки")
    target_language = st.selectbox(
        "Целевой язык",
        ["Русский", "English", "Español", "Français", "Deutsch", "中文"]
    )

    st.markdown("---")
    st.subheader("🔧 Настройки обработки")

    parallel_requests = st.slider(
        "Параллельных запросов",
        min_value=1, max_value=10, value=3, step=1
    )

    max_retries = st.slider(
        "Количество попыток",
        min_value=1, max_value=5, value=3
    )

    st.markdown("---")
    st.subheader("🎨 Настройки дизайна")

    font_size_adjustment = st.slider(
        "Корректировка размера шрифта",
        min_value=-3, max_value=3, value=0, step=1,
        help="Если переведенный текст не помещается"
    )

    preserve_font_color = st.checkbox(
        "Сохранить цвет шрифта",
        value=True
    )

# ============================================================
# Загрузка PDF
# ============================================================
uploaded_file = st.file_uploader("Загрузите PDF файл", type="pdf")


# ============================================================
# 🆕 Функция: Извлечение текста с координатами
# ============================================================
def extract_text_with_positions(pdf_bytes: bytes) -> List[Dict]:
    """
    Извлекает текст из PDF с координатами, шрифтами и цветами.
    Возвращает список блоков текста с метаданными.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    all_blocks = []

    for page_num in range(len(doc)):
        page = doc[page_num]

        # Извлекаем текстовые блоки с детальной информацией
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

        for block in blocks:
            if block["type"] == 0:  # Текстовый блок
                for line in block["lines"]:
                    for span in line["spans"]:
                        text = span["text"].strip()
                        if text:
                            all_blocks.append({
                                "page": page_num,
                                "text": text,
                                "bbox": span["bbox"],  # (x0, y0, x1, y1)
                                "font": span["font"],
                                "size": span["size"],
                                "color": span["color"],  # RGB color as integer
                                "flags": span["flags"]  # bold, italic, etc.
                            })

    doc.close()
    return all_blocks


# ============================================================
# 🆕 Функция: Группировка текста для перевода
# ============================================================
def group_text_for_translation(blocks: List[Dict], max_chars: int = 3000) -> List[Tuple[int, str]]:
    """
    Группирует текстовые блоки для перевода.
    Возвращает список (start_index, combined_text).
    """
    groups = []
    current_text = []
    current_length = 0
    start_index = 0

    for i, block in enumerate(blocks):
        text = block["text"]
        text_length = len(text)

        if current_length + text_length > max_chars and current_text:
            groups.append((start_index, " ".join(current_text)))
            current_text = [text]
            current_length = text_length
            start_index = i
        else:
            if not current_text:
                start_index = i
            current_text.append(text)
            current_length += text_length

    if current_text:
        groups.append((start_index, " ".join(current_text)))

    return groups


# ============================================================
# 🆕 Функция: Перевод одного блока текста
# ============================================================
def translate_text_block(
        text: str,
        target_lang: str,
        api_key: str,
        model,
        timeout: int,
        max_retries: int
) -> Tuple[str, bool]:
    """Переводит текст с retry логикой"""

    prompt = f"""Переведи следующий текст на {target_lang}.

ВАЖНО:
- Переводи только содержание
- Не добавляй комментариев или объяснений
- Сохрани смысл оригинала

Текст для перевода:
{text}"""

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model.id,
                messages=[
                    {"role": "system", "content": "Ты профессиональный переводчик."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                timeout=timeout
            )
            return response.choices[0].message.content, True

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                return None, False

    return None, False


# ============================================================
# 🆕 Функция: Параллельный перевод всех блоков
# ============================================================
def translate_all_blocks_parallel(
        blocks: List[Dict],
        target_lang: str,
        api_key: str,
        model,
        max_workers: int,
        timeout: int,
        max_retries: int,
        progress_callback
) -> List[str]:
    """
    Переводит все текстовые блоки параллельно.
    Возвращает список переведенных текстов в том же порядке.
    """
    # Группируем текст для эффективного перевода
    groups = group_text_for_translation(blocks)

    translated_groups = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_group = {
            executor.submit(
                translate_text_block,
                text, target_lang, api_key, model, timeout, max_retries
            ): (start_idx, text)
            for start_idx, text in groups
        }

        completed = 0
        for future in as_completed(future_to_group):
            start_idx, original_text = future_to_group[future]
            translated_text, success = future.result()

            if success:
                translated_groups[start_idx] = translated_text
            else:
                translated_groups[start_idx] = original_text  # Fallback

            completed += 1
            progress_callback(completed, len(groups))

    # Разбиваем переведенные группы обратно на отдельные блоки
    translated_blocks = []
    for start_idx, original_text in groups:
        translated_text = translated_groups.get(start_idx, original_text)

        # Разбиваем переведенный текст на слова
        translated_words = translated_text.split()

        # Считаем количество оригинальных блоков в этой группе
        group_blocks = []
        for i in range(start_idx, len(blocks)):
            if i > start_idx and i in [g[0] for g in groups]:
                break
            group_blocks.append(blocks[i])

        # Распределяем переведенные слова по блокам
        words_per_block = max(1, len(translated_words) // len(group_blocks))

        for j, block in enumerate(group_blocks):
            start_word = j * words_per_block
            end_word = start_word + words_per_block if j < len(group_blocks) - 1 else len(translated_words)
            block_translation = " ".join(translated_words[start_word:end_word])
            translated_blocks.append(block_translation)

    return translated_blocks


# ============================================================
# 🆕 Функция: Создание PDF с сохранением дизайна
# ============================================================
def create_translated_pdf(
        original_pdf_bytes: bytes,
        blocks: List[Dict],
        translated_texts: List[str],
        font_size_adjustment: int = 0,
        preserve_font_color: bool = True
) -> bytes:
    """
    Создает новый PDF с переведенным текстом, сохраняя дизайн оригинала.
    """
    doc = fitz.open(stream=original_pdf_bytes, filetype="pdf")

    # Группируем блоки по страницам
    blocks_by_page = defaultdict(list)
    for i, block in enumerate(blocks):
        blocks_by_page[block["page"]].append((i, block))

    # Обрабатываем каждую страницу
    for page_num in range(len(doc)):
        page = doc[page_num]
        page_blocks = blocks_by_page.get(page_num, [])

        if not page_blocks:
            continue

        # Для каждого текстового блока
        for block_idx, block in page_blocks:
            translated_text = translated_texts[block_idx]

            if not translated_text or translated_text == block["text"]:
                continue

            # Получаем координаты и стиль
            x0, y0, x1, y1 = block["bbox"]
            font_name = block["font"]
            font_size = block["size"] + font_size_adjustment
            color_int = block["color"]

            # Конвертируем цвет из int в RGB tuple
            if preserve_font_color:
                r = (color_int >> 16) & 0xFF
                g = (color_int >> 8) & 0xFF
                b = color_int & 0xFF
                color = (r / 255, g / 255, b / 255)
            else:
                color = (0, 0, 0)  # Черный

            # Закрашиваем область старым текстом (белым цветом)
            rect = fitz.Rect(x0, y0, x1, y1)
            page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1))

            # Вставляем переведенный текст
            try:
                # Пытаемся использовать стандартный шрифт
                page.insert_text(
                    fitz.Point(x0, y0 + font_size),
                    translated_text,
                    fontsize=font_size,
                    fontname="helv",  # Helvetica
                    color=color
                )
            except Exception as e:
                # Если не получилось, используем встроенный шрифт
                page.insert_text(
                    fitz.Point(x0, y0 + font_size),
                    translated_text,
                    fontsize=font_size,
                    color=color
                )

    # Сохраняем в bytes
    output_bytes = doc.tobytes()
    doc.close()

    return output_bytes


# ============================================================
# Основная логика приложения
# ============================================================
if uploaded_file is not None:
    st.session_state.original_pdf_bytes = uploaded_file.getvalue()
    st.success(f"✅ Файл загружен: {uploaded_file.name}")

    # Кнопка извлечения текста
    if st.button("📝 Извлечь текст с координатами", type="primary"):
        with st.spinner("🔄 Извлечение текста из PDF..."):
            try:
                blocks = extract_text_with_positions(st.session_state.original_pdf_bytes)
                st.session_state.text_blocks = blocks
                st.success(f"✅ Извлечено {len(blocks)} текстовых блоков")
            except Exception as e:
                st.error(f"❌ Ошибка при извлечении: {e}")

    # Отображение информации о извлеченном тексте
    if st.session_state.text_blocks:
        st.markdown("---")

        with st.expander("📊 Информация о документе", expanded=False):
            doc = fitz.open(stream=st.session_state.original_pdf_bytes, filetype="pdf")
            st.write(f"**Страниц:** {len(doc)}")
            st.write(f"**Текстовых блоков:** {len(st.session_state.text_blocks)}")

            # Показываем первые несколько блоков
            st.write("**Пример текста:**")
            for i, block in enumerate(st.session_state.text_blocks[:10]):
                st.text(f"[{i}] {block['text'][:100]}...")
            doc.close()

        # Кнопка перевода
        if st.session_state.translated_pdf_bytes is None:
            if st.button("🌍 Перевести с сохранением дизайна", type="primary"):
                if not api_key:
                    st.error("❌ Пожалуйста, введите OpenAI API ключ")
                else:
                    blocks = st.session_state.text_blocks

                    st.info(f"""
                    📦 Текстовых блоков: **{len(blocks)}**  
                    🚀 Параллельных запросов: **{parallel_requests}**
                    """)

                    progress_bar = st.progress(0)
                    status_text = st.empty()


                    def update_progress(completed, total):
                        progress_bar.progress(completed / total)
                        status_text.text(f"🔄 Переведено {completed} из {total} групп...")


                    # Параллельный перевод
                    start_time = time.time()

                    translated_texts = translate_all_blocks_parallel(
                        blocks=blocks,
                        target_lang=target_language,
                        api_key=api_key,
                        model=model,
                        max_workers=parallel_requests,
                        timeout=timeout,
                        max_retries=max_retries,
                        progress_callback=update_progress
                    )

                    elapsed_time = time.time() - start_time
                    st.success(f"✅ Перевод завершен за {elapsed_time:.1f} секунд")

                    # Создание PDF с сохранением дизайна
                    with st.spinner("🎨 Создание PDF с сохранением дизайна..."):
                        try:
                            translated_pdf_bytes = create_translated_pdf(
                                original_pdf_bytes=st.session_state.original_pdf_bytes,
                                blocks=blocks,
                                translated_texts=translated_texts,
                                font_size_adjustment=font_size_adjustment,
                                preserve_font_color=preserve_font_color
                            )

                            st.session_state.translated_pdf_bytes = translated_pdf_bytes
                            st.success(f"✅ PDF создан ({len(translated_pdf_bytes) / 1024:.1f} KB)")

                        except Exception as e:
                            st.error(f"❌ Ошибка при создании PDF: {e}")

# ============================================================
# Отображение результатов
# ============================================================
if st.session_state.translated_pdf_bytes:
    st.markdown("---")

    st.subheader("📥 Скачать результат")

    base_name = uploaded_file.name.replace('.pdf', '')

    # Предпросмотр
    with st.expander("👁️ Предпросмотр переведенного PDF", expanded=True):
        st.info("💡 PDF сохранен с оригинальным дизайном: фон, изображения, цвета, расположение элементов")

    # Кнопка скачивания
    st.download_button(
        label="📥 Скачать переведенный PDF с сохранением дизайна",
        data=st.session_state.translated_pdf_bytes,
        file_name=f"translated_{base_name}.pdf",
        mime="application/pdf",
        type="primary"
    )

    # Кнопка очистки
    st.markdown("---")
    if st.button("🗑️ Очистить и начать заново"):
        for key in ["original_pdf_bytes", "translated_pdf_bytes", "text_blocks"]:
            if key in st.session_state:
                del st.session_state[key]
        st.rerun()

# ============================================================
# Футер
# ============================================================
st.markdown("---")
st.markdown("""
**Как это работает:**
1. Извлекаем текст с координатами (bounding boxes) из каждой страницы
2. Переводим текст через AI параллельно
3. Создаем новый PDF:
   - Копируем оригинальные страницы (фон, изображения, графика)
   - Закрашиваем старый текст белым
   - Размещаем переведенный текст в тех же позициях
   - Сохраняем цвет шрифта (опционально)

**Что сохраняется:**
- ✅ Фон страницы
- ✅ Изображения и графика
- ✅ Цвета элементов
- ✅ Расположение текста
- ✅ Таблицы (частично)
- ✅ Колонтитулы

**Ограничения:**
- ⚠️ Если переведенный текст длиннее оригинала, может не поместиться
- ⚠️ Сложные таблицы могут потребовать ручной корректировки
- ⚠️ Некоторые шрифты могут не поддерживаться

**Советы:**
- Используйте "Корректировка размера шрифта" если текст не помещается
- Отключите "Сохранить цвет шрифта" для лучшей читаемости
""")