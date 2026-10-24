import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import router from "./router";
import "./assets/main.css";
import "./assets/cafe-theme.css";
import "./assets/mobile.css";
import focusTrap from "./directives/focus-trap";
createApp(App)
  .use(createPinia())
  .use(router)
  .directive("focus-trap", focusTrap)
  .mount("#app");
