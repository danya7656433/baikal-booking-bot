from datetime import datetime


class RateLimitMiddleware:
    def __init__(self):
        self.requests = {}

    async def __call__(self, handler, event, data):
        user_id = event.from_user.id
        current_time = datetime.now()
        if user_id not in self.requests:
            self.requests[user_id] = []

        self.requests[user_id] = [
            request_time
            for request_time in self.requests[user_id]
            if (current_time - request_time).total_seconds() < 1
        ]
        if len(self.requests[user_id]) >= 3:
            await event.answer("⚠️ Пожалуйста, переключайте медленнее.")
            return

        self.requests[user_id] = [
            request_time
            for request_time in self.requests[user_id]
            if (current_time - request_time).total_seconds() < 3600
        ]
        if len(self.requests[user_id]) >= 100:
            await event.answer(
                "⚠️ Слишком много запросов. Пожалуйста, попробуйте позже."
            )
            return

        self.requests[user_id].append(current_time)
        return await handler(event, data)
