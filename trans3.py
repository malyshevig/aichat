import streamlit as st
from docling.document_converter import DocumentConverter
import openai
from typing import List, Tuple, Dict
import time
import tempfile
import os
import markdown
from weasyprint import HTML
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

url = "http://localhost:8000/v1"
timeout=1240
client = openai.OpenAI(
    base_url=url,  # адрес vLLM сервера
    api_key="no-key",  # любой непустой строки, если не задан --api-key
    timeout=1240
)
model = client.models.list().data[0]
model_name = model.model_extra['root']
max_model_len = model.model_extra["max_model_len"]

# ============================================================
# Настройка страницы
# ============================================================
st.set_page_config(page_title="PDF Переводчик с Docling", page_icon="📄", layout="wide")

# ============================================================
# Инициализация сессионного состояния
# ============================================================
if "markdown_content" not in st.session_state:
    st.session_state.markdown_content = None
if "translated_markdown" not in st.session_state:
    st.session_state.translated_markdown = None
if "translated_chunks" not in st.session_state:
    st.session_state.translated_chunks = []
if "current_chunk_index" not in st.session_state:
    st.session_state.current_chunk_index = 0
if "processing_complete" not in st.session_state:
    st.session_state.processing_complete = False

st.title("📄 PDF Переводчик с Docling + AI")

# ============================================================
# Боковая панель
# ============================================================
with st.sidebar:
    st.header("⚙️ Настройки")
    api_key = "none"
    #api_key = st.text_input("OpenAI API Key", type="password")
    target_language = st.selectbox(
        "Целевой язык",
        ["Русский", "English", "Español", "Français", "Deutsch", "中文"]
    )
    #model = st.selectbox(
    #    "Модель",
    #    ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"]
    #)

    st.markdown("---")
    st.subheader("🔧 Настройки обработки")
    chunk_size = st.slider(
        "Размер чанка (символы)",
        min_value=1000, max_value=32000, value=3000, step=500,
        help="Меньше = быстрее, но больше запросов"
    )

    # 🆕 Настройка параллелизма
    parallel_requests = st.slider(
        "Параллельных запросов",
        min_value=1, max_value=10, value=3, step=1,
        help="Больше = быстрее, но может упереться в rate limit"
    )

    #timeout = st.slider(
    #    "Timeout (секунды)",
    #    min_value=30, max_value=300, value=120, step=10
    #)
    max_retries = st.slider(
        "Количество попыток",
        min_value=1, max_value=5, value=3
    )

    st.markdown("---")
    st.subheader("📥 Формат экспорта")
    export_format = st.radio(
        "Скачать как:",
        ["Markdown", "PDF"],
        horizontal=True
    )



# ============================================================
# Загрузка PDF
# ============================================================
uploaded_file = st.file_uploader("Загрузите PDF файл", type="pdf")


# ============================================================
# Функция: PDF → Markdown (через Docling)
# ============================================================
def convert_pdf_to_markdown(pdf_file) -> str:
    """Конвертирует PDF в Markdown с помощью Docling"""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
        tmp_file.write(pdf_file.getvalue())
        tmp_path = tmp_file.name

    try:
        converter = DocumentConverter()
        result = converter.convert(tmp_path)
        return result.document.export_to_markdown()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# ============================================================
# Функция: разбиение Markdown на чанки
# ============================================================
def split_markdown_into_chunks(md: str, chunk_size: int = 3000) -> List[str]:
    """Разбивает Markdown на части для обработки"""
    lines = md.split('\n')
    chunks, current_chunk, current_length = [], [], 0

    for line in lines:
        line_length = len(line)
        if current_length + line_length > chunk_size and current_chunk:
            chunks.append('\n'.join(current_chunk))
            current_chunk, current_length = [line], line_length
        else:
            current_chunk.append(line)
            current_length += line_length

    if current_chunk:
        chunks.append('\n'.join(current_chunk))
    return chunks


# ============================================================
# 🆕 Функция: перевод одного чанка (для ThreadPoolExecutor)
# ============================================================
def translate_single_chunk(
        chunk_index: int,
        chunk: str,
        target_lang: str,
        model,
        timeout: int,
        max_retries: int
) -> Tuple[int, str, bool]:
    """
    Переводит один чанк с retry логикой.
    Возвращает (индекс_чанка, переведенный_текст, успех)
    """

    prompt = f"""Переведи следующий Markdown текст на {target_lang}.

ВАЖНО:
- Сохрани всю Markdown разметку (# заголовки, **жирный**, *курсив*, - списки, таблицы)
- Сохрани структуру документа
- Переводи только текстовое содержимое
- Не добавляй комментариев или объяснений

Текст для перевода:

{chunk}"""

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model.id,
                messages=[
                    {"role": "system",
                     "content": "Ты профессиональный переводчик. Переводи точно, сохраняя Markdown разметку и структуру документа."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                timeout=timeout
            )
            return chunk_index, response.choices[0].message.content, True

        except openai.APITimeoutError:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                time.sleep(wait_time)
            else:
                return chunk_index, None, False

        except openai.APIError:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                time.sleep(wait_time)
            else:
                return chunk_index, None, False

        except Exception:
            return chunk_index, None, False

    return chunk_index, None, False


# ============================================================
# 🆕 Функция: параллельный перевод всех чанков
# ============================================================
def translate_chunks_parallel(
        chunks: List[str],
        target_lang: str,
        api_key: str,
        model,
        max_workers: int,
        timeout: int,
        max_retries: int,
        progress_callback
) -> Tuple[List[str], int, int]:
    """
    Переводит все чанки параллельно.
    Возвращает (список_переведенных_чанков, количество_успешных, количество_неудачных)
    """
    results = {}  # {chunk_index: translated_text}
    failed_indices = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Создаем futures для всех чанков
        future_to_index = {
            executor.submit(
                translate_single_chunk,
                i, chunk, target_lang, model, timeout, max_retries
            ): i
            for i, chunk in enumerate(chunks)
        }

        # Обрабатываем завершенные futures
        completed = 0
        for future in as_completed(future_to_index):
            chunk_index, translated_text, success = future.result()

            if success:
                results[chunk_index] = translated_text
            else:
                failed_indices.append(chunk_index)

            completed += 1
            progress_callback(completed, len(chunks))

    # Восстанавливаем порядок чанков
    ordered_results = [results.get(i, "") for i in range(len(chunks))]

    return ordered_results, len(results), len(failed_indices)


# ============================================================
# Функция: Markdown → PDF (через WeasyPrint)
# ============================================================
def markdown_to_pdf(markdown_text: str, title: str = "Document") -> bytes:
    """Конвертирует Markdown в PDF через HTML + WeasyPrint"""
    html_body = markdown.markdown(
        markdown_text,
        extensions=['tables', 'fenced_code', 'toc', 'nl2br']
    )

    html_full = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
    <style>
        @page {{
            size: A4;
            margin: 2cm;
            @bottom-center {{
                content: counter(page) " / " counter(pages);
                font-size: 9pt;
                color: #888;
            }}
        }}
        body {{
            font-family: "DejaVu Sans", "Noto Sans", "Arial", "Helvetica", sans-serif;
            font-size: 11pt;
            line-height: 1.6;
            color: #222;
            text-align: justify;
        }}
        h1 {{
            font-size: 22pt;
            color: #1a1a1a;
            border-bottom: 2px solid #333;
            padding-bottom: 6px;
            margin-top: 20px;
            page-break-after: avoid;
        }}
        h2 {{
            font-size: 17pt;
            color: #2a2a2a;
            margin-top: 16px;
            page-break-after: avoid;
        }}
        h3 {{
            font-size: 14pt;
            color: #333;
            margin-top: 12px;
            page-break-after: avoid;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            margin: 1em 0;
            font-size: 10pt;
            page-break-inside: avoid;
        }}
        th, td {{
            border: 1px solid #ccc;
            padding: 6px 10px;
            text-align: left;
        }}
        th {{
            background-color: #f0f0f0;
            font-weight: bold;
        }}
        code {{
            font-family: "DejaVu Sans Mono", "Courier New", monospace;
            background-color: #f5f5f5;
            padding: 2px 5px;
            border-radius: 3px;
            font-size: 10pt;
        }}
        pre {{
            background-color: #f5f5f5;
            padding: 10px;
            border-radius: 4px;
            overflow-x: auto;
            font-size: 9pt;
            page-break-inside: avoid;
        }}
    </style>
</head>
<body>
{html_body}
</body>
</html>"""

    pdf_bytes = HTML(string=html_full).write_pdf()
    return pdf_bytes


# ============================================================
# Основная логика приложения
# ============================================================
if uploaded_file is not None:
    st.success(f"✅ Файл загружен: {uploaded_file.name}")

    # Кнопка конвертации PDF → Markdown
    if st.button("📝 Конвертировать в Markdown", type="primary"):
        with st.spinner("🔄 Конвертация PDF в Markdown с помощью Docling..."):
            try:
                markdown_content = convert_pdf_to_markdown(uploaded_file)
                st.session_state.markdown_content = markdown_content
                st.session_state.translated_chunks = []
                st.session_state.current_chunk_index = 0
                st.session_state.processing_complete = False
                st.success("✅ Конвертация завершена!")
            except Exception as e:
                st.error(f"❌ Ошибка при конвертации: {e}")

    # Отображение исходного Markdown
    if st.session_state.markdown_content:
        st.markdown("---")

        with st.expander("📄 Исходный Markdown (нажмите для просмотра)", expanded=False):
            st.markdown(st.session_state.markdown_content)

            st.download_button(
                label="📥 Скачать оригинальный Markdown",
                data=st.session_state.markdown_content,
                file_name=f"original_{uploaded_file.name.replace('.pdf', '.md')}",
                mime="text/markdown"
            )

        # Кнопка перевода
        if not st.session_state.processing_complete:
            if st.button("🌍 Перевести Markdown (параллельно)", type="primary"):
                if not api_key:
                    st.error("❌ Пожалуйста, введите OpenAI API ключ в боковой панели")
                else:
                    chunks = split_markdown_into_chunks(st.session_state.markdown_content, chunk_size)
                    total_chunks = len(chunks)

                    st.info(f"""
                    📦 Текст разбит на **{total_chunks}** частей  
                    🚀 Параллельных запросов: **{parallel_requests}**  
                    ⚡ Ожидаемое ускорение: **~{min(parallel_requests, total_chunks)}x**
                    """)

                    # Прогресс-бар
                    progress_bar = st.progress(0)
                    status_text = st.empty()


                    # Callback для обновления прогресса
                    def update_progress(completed, total):
                        progress_bar.progress(completed / total)
                        status_text.text(f"🔄 Переведено {completed} из {total} чанков...")


                    # Параллельный перевод
                    start_time = time.time()

                    translated_chunks, successful, failed = translate_chunks_parallel(
                        chunks=chunks,
                        target_lang=target_language,
                        api_key=api_key,
                        model=model,
                        max_workers=parallel_requests,
                        timeout=timeout,
                        max_retries=max_retries,
                        progress_callback=update_progress
                    )

                    elapsed_time = time.time() - start_time

                    # Результаты
                    st.session_state.translated_chunks = translated_chunks
                    st.session_state.processing_complete = True

                    if failed == 0:
                        # Все чанки переведены успешно
                        full_translated_markdown = "\n\n".join(translated_chunks)
                        st.session_state.translated_markdown = full_translated_markdown

                        st.success(f"""
                        ✅ Перевод завершен!  
                        ⏱️ Время: **{elapsed_time:.1f}** секунд  
                        📊 Скорость: **{total_chunks / elapsed_time:.2f}** чанков/сек
                        """)
                    else:
                        # Некоторые чанки не переведены
                        st.warning(f"""
                        ⚠️ Перевод завершен с ошибками  
                        ✅ Успешно: **{successful}** чанков  
                        ❌ Не удалось: **{failed}** чанков  
                        ⏱️ Время: **{elapsed_time:.1f}** секунд
                        """)

                        # Показываем, какие чанки не переведены
                        failed_indices = [i for i, chunk in enumerate(translated_chunks) if not chunk]
                        st.error(f"Не переведены чанки: {', '.join(map(str, failed_indices))}")

                        # Объединяем то, что есть
                        valid_chunks = [chunk for chunk in translated_chunks if chunk]
                        if valid_chunks:
                            partial_markdown = "\n\n".join(valid_chunks)
                            st.session_state.translated_markdown = partial_markdown
                            st.info("💡 Вы можете скачать частичный результат или повторить перевод")

# ============================================================
# Отображение результатов и кнопки скачивания
# ============================================================
if st.session_state.translated_markdown:
    st.markdown("---")

    tab1, tab2 = st.tabs(["📄 Оригинал (Markdown)", "🌍 Перевод (Markdown)"])

    with tab1:
        st.markdown("### Исходный документ:")
        st.markdown(st.session_state.markdown_content)

    with tab2:
        st.markdown("### Переведенный документ:")
        st.markdown(st.session_state.translated_markdown)

    st.markdown("---")

    # Блок скачивания
    st.subheader("📥 Скачать результат")

    base_name = os.path.splitext(uploaded_file.name)[0]

    if export_format == "Markdown":
        st.download_button(
            label="📥 Скачать переведенный Markdown (.md)",
            data=st.session_state.translated_markdown,
            file_name=f"translated_{base_name}.md",
            mime="text/markdown",
            type="primary"
        )

    else:  # PDF
        with st.spinner("🔄 Генерация PDF из Markdown..."):
            try:
                pdf_bytes = markdown_to_pdf(
                    st.session_state.translated_markdown,
                    title=f"Translated: {base_name}"
                )

                st.success(f"✅ PDF сгенерирован ({len(pdf_bytes) / 1024:.1f} KB)")

                with st.expander("👁️ Предпросмотр содержимого PDF", expanded=False):
                    st.markdown(st.session_state.translated_markdown)

                st.download_button(
                    label="📥 Скачать переведенный PDF (.pdf)",
                    data=pdf_bytes,
                    file_name=f"translated_{base_name}.pdf",
                    mime="application/pdf",
                    type="primary"
                )

                st.download_button(
                    label="📄 Также скачать Markdown (.md)",
                    data=st.session_state.translated_markdown,
                    file_name=f"translated_{base_name}.md",
                    mime="text/markdown"
                )

            except Exception as e:
                st.error(f"❌ Ошибка при генерации PDF: {e}")
                st.info("💡 Попробуйте скачать как Markdown")

    # Кнопка очистки
    st.markdown("---")
    if st.button("🗑️ Очистить и начать заново"):
        for key in ["markdown_content", "translated_markdown", "translated_chunks",
                    "current_chunk_index", "processing_complete"]:
            if key in st.session_state:
                del st.session_state[key]
        st.rerun()

# ============================================================
# Футер
# ============================================================
st.markdown("---")
st.markdown("""
**Как использовать:**
1. Введите OpenAI API ключ в боковой панели
2. Выберите целевой язык перевода
3. Загрузите PDF файл
4. Нажмите **"Конвертировать в Markdown"**
5. Настройте количество параллельных запросов (рекомендуется 3-5)
6. Нажмите **"Перевести Markdown (параллельно)"**
7. Выберите формат экспорта: **Markdown** или **PDF**
8. Скачайте результат

**Преимущества параллельной обработки:**
- ⚡ Ускорение в 3-5 раз по сравнению с последовательной обработкой
- 🔄 Автоматическая обработка ошибок с retry
- 📊 Сохранение порядка чанков в результате
- 🛡️ Защита от rate limit через настройку параллелизма
""")