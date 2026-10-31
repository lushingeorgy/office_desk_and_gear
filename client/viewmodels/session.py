"""UI-independent session commands and cache boundaries for the mobile views."""
from __future__ import annotations


class SessionViewModel:
    def __init__(self, api, state, worker, account_context):
        self.api, self.state, self.worker = api, state, worker
        self.account_context = account_context

    def restore(self):
        token, user = self.state.account()
        self.api.set_token(token)
        return user

    def authenticate(self, email, password):
        result = self.api.login(email, password)
        self.api.set_token(result["access_token"])
        try:
            return result, self.api.get_me()
        except Exception:
            self.api.set_token(None)
            raise

    def remember(self, token_result, user):
        self.state.save_account(token_result["access_token"], user, token_result.get("expires_at"))

    def forget(self):
        self.api.set_token(None)
        self.api.close()
        self.state.clear_account()

    def cached(self, key):
        return self.state.cache_get(key) if key else None

    def execute(self, function, success, failure, *, cache_key=None):
        context = self.account_context()

        def completed(value):
            # A result from a prior user, server, or revoked bearer never enters another cache.
            if cache_key and self.account_context() != context:
                return success(None, True)
            if cache_key:
                self.state.cache_put(cache_key, value)
            success(value, False)

        def rejected(error):
            if self.account_context() != context:
                return success(None, True)
            failure(error)
        return self.worker.submit(function, completed, rejected)

    def invalidate(self):
        self.state.data.pop("cache", None)
        self.state.flush()

    def pause(self):
        self.state.flush()
        self.api.close()

    def close(self):
        self.worker.stop()
        self.pause()
