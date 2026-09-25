// Testes visuais do painel (painel_precos.html). Rodam contra o arquivo local,
// com o Supabase, a biblioteca e as imagens interceptados (ver
// tests/visual/painel.spec.js): nada sai para a rede e os dados sao sempre os
// mesmos, entao a comparacao pode ser pixel a pixel.
const { defineConfig, devices } = require("@playwright/test");

const comum = {
  locale: "pt-BR",
  timezoneId: "America/Sao_Paulo",
};

module.exports = defineConfig({
  testDir: "tests/visual",
  outputDir: "tests/visual/resultados",
  // Referencias por sistema: fonte e antialiasing mudam entre Linux e macOS.
  snapshotPathTemplate: "{testDir}/referencias/{platform}/{projectName}/{arg}{ext}",
  fullyParallel: true,
  forbidOnly: true,
  reporter: [["list"], ["html", { outputFolder: "tests/visual/relatorio", open: "never" }]],
  expect: {
    // Pixel perfect: nenhum pixel diferente, nenhuma tolerancia de cor.
    toHaveScreenshot: { maxDiffPixels: 0, threshold: 0, animations: "disabled", caret: "hide" },
  },
  use: {
    ...comum,
    trace: "retain-on-failure",
    // O antialiasing subpixel (LCD) varia de uma execucao para outra na borda
    // dos glifos; em tons de cinza e sem hinting o desenho e sempre o mesmo.
    launchOptions: { args: ["--disable-lcd-text", "--font-render-hinting=none"] },
  },
  projects: [
    { name: "desktop-claro", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 900 }, colorScheme: "light" } },
    { name: "desktop-escuro", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 900 }, colorScheme: "dark" } },
    { name: "celular-claro", use: { ...devices["Pixel 7"], colorScheme: "light" } },
    { name: "celular-escuro", use: { ...devices["Pixel 7"], colorScheme: "dark" } },
  ],
});
