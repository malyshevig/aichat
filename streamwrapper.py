from typing import Iterator, Optional, Any
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk


class StreamWithUsage:
    """
    Враппер вокруг OpenAI stream, который:
    1. Ведёт себя как генератор (совместим со st.write_stream)
    2. Накапливает usage в атрибуте .usage после завершения итерации
    """

    def __init__(self, stream: Iterator[ChatCompletionChunk]):
        self._stream = stream
        self.usage: Optional[Any] = None
        self._full_content: str = ""

    def __iter__(self):
        return self

    def __next__(self) -> str:
        """Отдаёт следующий кусочек контента (для st.write_stream)"""
        while True:
            chunk = next(self._stream)

            # Ловим usage в последнем чанке
            if chunk.usage is not None:
                self.usage = chunk.usage
                # Если в этом же чанке есть контент — отдадим его
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    self._full_content += content
                    return content
                # Иначе идём к следующему чанку (usage обычно в последнем)
                raise StopIteration

            # Если есть контент — отдаём
            if chunk.choices and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                self._full_content += content
                return content
            # Иначе пропускаем пустые чанки

    @property
    def full_content(self) -> str:
        """Полный собранный текст после завершения стрима"""
        return self._full_content