import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import router from "./router";
import "./assets/main.css";
import "./assets/cafe-theme.css";
import "./assets/responsive.css";
import focusTrap from "./directives/focus-trap";
import { resetLegacyWakeProfiles } from "./api/wake-profile";
resetLegacyWakeProfiles();
createApp(App)
  .use(createPinia())
  .use(router)
  .directive("focus-trap", focusTrap)
  .mount("#app");
