(() => {
  const defaults = {theme:'dark', language:'zh', autosave_interval_minutes:20};
  let current = {...defaults};
  let loadPromise = null;
  async function applyGlobalPreferences(){
    if(loadPromise) return loadPromise;
    loadPromise = (async () => {
      try {
        const response = await fetch('/api/global-settings/preferences', {cache:'no-store'});
        if(response.ok) current = {...defaults, ...((await response.json()).preferences || {})};
      } catch(error) {}
      current.theme = current.theme === 'light' ? 'light' : 'dark';
      current.language = current.language === 'en' ? 'en' : 'zh';
      const interval = Number.parseInt(current.autosave_interval_minutes, 10);
      current.autosave_interval_minutes = Number.isInteger(interval) && interval >= 1 && interval <= 240 ? interval : defaults.autosave_interval_minutes;
      localStorage.setItem('studio_theme', current.theme);
      localStorage.setItem('canvas_theme', current.theme);
      localStorage.setItem('studio_lang', current.language);
      window.StudioTheme?.apply(current.theme);
      window.StudioI18n?.set(current.language);
      return {...current};
    })();
    return loadPromise;
  }
  window.applyGlobalPreferences = applyGlobalPreferences;
  window.FireflyGlobalPreferences = {load: applyGlobalPreferences, get: () => ({...current})};
  applyGlobalPreferences();
})();
