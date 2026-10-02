<template>
  <template v-if="session.status === 'authenticated'">
    <router-view />
    <div class="session-control">
      <p v-if="logoutError" role="alert">{{ logoutError }}</p>
      <button type="button" aria-label="Sign out" :disabled="busy" @click="signOut">
        {{ busy ? 'Signing out…' : 'Sign out' }}
      </button>
    </div>
  </template>
  <main v-else class="access-screen">
    <section class="access-card" aria-labelledby="access-title">
      <p class="access-brand">MIROFISH</p>
      <h1 id="access-title">Private workspace</h1>
      <p v-if="session.status === 'checking' && !verificationError" role="status">Checking your session…</p>
      <template v-else-if="verificationError">
        <p role="alert">{{ verificationError }}</p>
        <button type="button" class="access-submit" @click="verifySession">Retry</button>
      </template>
      <template v-else-if="!session.configured">
        <p>Set <code>MIROFISH_ACCESS_KEY</code> on the server and restart it to enable sign-in.</p>
        <button type="button" class="access-submit" @click="verifySession">Reload</button>
      </template>
      <form v-else @submit.prevent="signIn">
        <p>Enter your access key to continue.</p>
        <label for="access-key">Access key</label>
        <input
          id="access-key"
          v-model="accessKey"
          type="password"
          name="access_key"
          autocomplete="current-password"
          spellcheck="false"
          :disabled="busy"
          :aria-describedby="loginError ? 'login-error' : undefined"
          required
        />
        <p v-if="loginError" id="login-error" class="access-error" role="alert">{{ loginError }}</p>
        <button type="submit" class="access-submit" :disabled="busy || !accessKey">
          {{ busy ? 'Signing in…' : 'Sign in' }}
        </button>
      </form>
    </section>
  </main>
</template>

<script setup>
import { ref } from 'vue'
import service from './api'
import { session, beginSessionCheck, applySession, clearSession } from './auth/session'

const accessKey = ref('')
const busy = ref(false)
const loginError = ref('')
const logoutError = ref('')
const verificationError = ref('')

async function verifySession() {
  beginSessionCheck()
  verificationError.value = ''
  try {
    const result = await service.get('/api/auth/session')
    applySession(result.data)
  } catch {
    verificationError.value = 'Unable to verify your session. Check the server connection and retry.'
  }
}

async function signIn() {
  if (busy.value || !accessKey.value) return
  busy.value = true
  loginError.value = ''
  const submittedKey = accessKey.value
  accessKey.value = ''
  try {
    const result = await service.post('/api/auth/login', { access_key: submittedKey })
    applySession(result.data)
  } catch (error) {
    loginError.value = error.message || 'Unable to sign in. Please try again.'
  } finally {
    busy.value = false
  }
}

async function signOut() {
  if (busy.value) return
  busy.value = true
  logoutError.value = ''
  try {
    await service.post('/api/auth/logout')
    clearSession()
  } catch {
    logoutError.value = 'Unable to sign out. Please retry.'
  } finally {
    busy.value = false
  }
}

verifySession()
</script>

<style scoped>
.access-screen {
  display: grid;
  min-height: 100dvh;
  place-items: center;
  padding: 24px;
  background: #f7f7f5;
}
.access-card {
  width: min(100%, 420px);
  padding: 36px;
  border: 1px solid #deded9;
  background: #fff;
}
.access-brand { font-size: 12px; letter-spacing: .14em; font-weight: 700; }
.access-card h1 { margin: 16px 0; font-size: 25px; letter-spacing: -.04em; }
.access-card p { font-size: 13px; line-height: 1.7; }
.access-card label { display: block; margin: 24px 0 8px; font-size: 12px; font-weight: 600; }
.access-card input {
  width: 100%;
  padding: 12px;
  font: inherit;
  border: 1px solid #aaa;
  border-radius: 0;
}
.access-card input:focus-visible, .access-card button:focus-visible, .session-control button:focus-visible {
  outline: 2px solid #245cc9;
  outline-offset: 3px;
}
.access-submit {
  width: 100%;
  margin-top: 20px;
  padding: 12px;
  border: 1px solid #171717;
  background: #171717;
  color: #fff;
  cursor: pointer;
}
.access-submit:disabled { opacity: .5; cursor: default; }
.access-error { margin-top: 12px; color: #a62323; }
.session-control { position: fixed; right: 16px; bottom: 16px; z-index: 100; }
.session-control button { padding: 6px 10px; border: 1px solid #ddd; background: #fff; color: #555; cursor: pointer; font-size: 11px; }
.session-control p { max-width: 240px; margin-bottom: 8px; padding: 8px; background: #fff; color: #a62323; font-size: 12px; }
@media (max-width: 480px) { .access-card { padding: 24px; } }
</style>

<style>
/* 全局样式重置 */
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

#app {
  font-family: 'JetBrains Mono', 'Space Grotesk', 'Noto Sans SC', monospace;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  color: #000000;
  background-color: #ffffff;
}

/* 滚动条样式 */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

::-webkit-scrollbar-track {
  background: #f1f1f1;
}

::-webkit-scrollbar-thumb {
  background: #000000;
}

::-webkit-scrollbar-thumb:hover {
  background: #333333;
}

/* 全局按钮样式 */
button {
  font-family: inherit;
}
</style>
