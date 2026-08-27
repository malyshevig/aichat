import streamlit as st
from docling.document_converter import DocumentConverter
from openai import OpenAI
from typing import List
import time

url = "http://localhost:8000/v1"
client = OpenAI(
    base_url=url,  # адрес vLLM сервера
    api_key="no-key",  # любой непустой строки, если не задан --api-key
    timeout=1240.0
)

model = client.models.list().data[0]
model_name = model.model_extra['root']
max_model_len = model.model_extra["max_model_len"]
# Настройка страницы
st.set_page_config(page_title="PDF Переводчик с Docling", page_icon="📄", layout="wide")

# Инициализация сессионного состояния
if "markdown_content" not in st.session_state:
    st.session_state.markdown_content = None
if "translated_markdown" not in st.session_state:
    st.session_state.translated_markdown = None

st.title("📄 PDF Переводчик с Docling + AI")
api_key = "None"

# Боковая панель для настроек
with st.sidebar:
    st.header("⚙️ Настройки")
#    api_key = st.text_input("OpenAI API Key", type="password")
    target_language = st.selectbox(
        "Целевой язык",
        ["Русский", "English", "Español", "Français", "Deutsch", "中文"]
    )
    #model = st.selectbox(
    #    "Модель",
    #   ["gpt-3.5-turbo", "gpt-4", "gpt-4-turbo-preview"]
    #)

    st.markdown("---")
    st.markdown("""
    **О приложении:**
    - Использует Docling для извлечения текста
    - Конвертирует PDF → Markdown
    - Переводит через AI
    - Сохраняет структуру документа
    """)

# Загрузка PDF
uploaded_file = st.file_uploader("Загрузите PDF файл", type="pdf")


def convert_pdf_to_markdown(pdf_file) -> str:
    """Конвертирует PDF в Markdown с помощью Docling"""
    # Сохраняем загруженный файл во временный файл
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
        tmp_file.write(pdf_file.getvalue())
        tmp_path = tmp_file.name

    try:
        # Инициализация конвертера
        converter = DocumentConverter()

        # Конвертация PDF в Markdown
        result = converter.convert(tmp_path)

        # Получаем Markdown текст
        markdown_text = result.document.export_to_markdown()

        return markdown_text
    finally:
        # Удаляем временный файл
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def split_markdown_into_chunks(markdown: str, max_tokens: int = 3000) -> List[str]:
    """Разбивает Markdown на части для обработки, стараясь не разрывать блоки"""
    # Разбиваем по заголовкам или параграфам
    lines = markdown.split('\n')
    chunks = []
    current_chunk = []
    current_length = 0

    for line in lines:
        line_length = len(line)

        # Если добавление этой строки превысит лимит
        if current_length + line_length > max_tokens * 3 and current_chunk:
            chunks.append('\n'.join(current_chunk))
            current_chunk = [line]
            current_length = line_length
        else:
            current_chunk.append(line)
            current_length += line_length

    # Добавляем последний кусок
    if current_chunk:
        chunks.append('\n'.join(current_chunk))

    return chunks


def translate_markdown(markdown: str, target_lang: str, api_key: str, model) -> str:
    """Переводит Markdown текст, сохраняя разметку"""

    prompt = f"""Переведи следующий Markdown текст на {target_lang}.

    ВАЖНО:
    - Сохрани всю Markdown разметку (# заголовки, **жирный**, *курсив*, - списки, таблицы)
    - Сохрани структуру документа
    - Переводи только текстовое содержимое
    - Не добавляй комментариев или объяснений

    Текст для перевода:

{markdown}"""

    try:
        response = client.chat.completions.create(
            model=model.id,
            messages=[
                {"role": "system",
                 "content": "Ты профессиональный переводчик. Переводи точно, сохраняя Markdown разметку и структуру документа."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
        )
        return response.choices[0].message.content
    except Exception as e:
        st.error(f"Ошибка при переводе: {e}")
        return None


# Основная логика приложения
if uploaded_file is not None:
    st.success(f"✅ Файл загружен: {uploaded_file.name}")

    # Кнопка для конвертации в Markdown
    if st.button("📝 Конвертировать в Markdown", type="primary"):
        with st.spinner("🔄 Конвертация PDF в Markdown с помощью Docling..."):
            try:
                markdown_content = convert_pdf_to_markdown(uploaded_file)
                st.session_state.markdown_content = markdown_content
                st.success("✅ Конвертация завершена!")
            except Exception as e:
                st.error(f"❌ Ошибка при конвертации: {e}")

    # Отображение Markdown и кнопка перевода
    if st.session_state.markdown_content:
        st.markdown("---")

        # Показываем оригинальный Markdown
        with st.expander("📄 Исходный Markdown (нажмите для просмотра)", expanded=False):
            st.markdown(st.session_state.markdown_content)

            # Кнопка скачивания оригинального Markdown
            st.download_button(
                label="📥 Скачать оригинальный Markdown",
                data=st.session_state.markdown_content,
                file_name=f"original_{uploaded_file.name.replace('.pdf', '.md')}",
                mime="text/markdown"
            )

        # Кнопка перевода
        if st.button("🌍 Перевести Markdown", type="primary"):
            if not api_key:
                st.error("❌ Пожалуйста, введите OpenAI API ключ в боковой панели")
            else:
                # Разбиваем на части
                chunks = split_markdown_into_chunks(st.session_state.markdown_content)
                st.info(f"📦 Текст разбит на {len(chunks)} частей для обработки")

                # Переводим каждую часть
                translated_chunks = []
                progress_bar = st.progress(0)

                for i, chunk in enumerate(chunks):
                    with st.spinner(f"🔄 Перевод части {i + 1} из {len(chunks)}..."):
                        translated = translate_markdown(chunk, target_language, api_key, model)
                        if translated:
                            translated_chunks.append(translated)
                        else:
                            st.error(f"Ошибка при переводе части {i + 1}")
                            break

                    # Обновляем прогресс
                    progress_bar.progress((i + 1) / len(chunks))
                    time.sleep(0.5)  # Задержка для избежания rate limit

                if len(translated_chunks) == len(chunks):
                    # Объединяем переведенные части
                    full_translated_markdown = "\n\n".join(translated_chunks)
                    st.session_state.translated_markdown = full_translated_markdown
                    st.success("✅ Перевод завершен!")

# Отображение результатов перевода
if st.session_state.translated_markdown:
    st.markdown("---")

    # Вкладки для сравнения
    tab1, tab2 = st.tabs(["📄 Оригинал (Markdown)", "🌍 Перевод (Markdown)"])

    with tab1:
        st.markdown("### Исходный документ:")
        st.markdown(st.session_state.markdown_content)

    with tab2:
        st.markdown("### Переведенный документ:")
        st.markdown(st.session_state.translated_markdown)

    # Кнопки скачивания
    st.markdown("---")
    col1, col2 = st.columns(2)

    with col1:
        st.download_button(
            label="📥 Скачать переведенный Markdown",
            data=st.session_state.translated_markdown,
            file_name=f"translated_{uploaded_file.name.replace('.pdf', '.md')}",
            mime="text/markdown"
        )

    with col2:
        if st.button("🗑️ Очистить и начать заново"):
            st.session_state.markdown_content = None
            st.session_state.translated_markdown = None
            st.rerun()

# Информация в футере
st.markdown("---")
st.markdown("""
**Как использовать:**
1. Введите OpenAI API ключ в боковой панели
2. Выберите целевой язык перевода
3. Загрузите PDF файл
4. Нажмите "Конвертировать в Markdown"
5. Просмотрите результат конвертации
6. Нажмите "Перевести Markdown"
7. Скачайте переведенный Markdown файл

**Преимущества Docling:**
- ✅ Сохраняет структуру документа (заголовки, списки, таблицы)
- ✅ Лучшее качество извлечения текста
- ✅ Поддержка сложных форматов
- ✅ Промежуточный Markdown для проверки
""")