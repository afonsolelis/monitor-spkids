// Regressao visual do painel de precos. Cada teste abre painel_precos.html do
// disco com a rede toda interceptada:
//   - Supabase: painel_precos devolve fixtures/painel.json (gravado do banco
//     real), marcacoes fixas, escrita respondida sem tocar no banco;
//   - supabase-js: servido do node_modules em vez do CDN;
//   - imagens das cartas: um retangulo cinza;
//   - qualquer outro pedido: abortado.
// O relogio fica parado para a projecao da tendencia nao mudar a cada dia.
const fs = require("node:fs");
const path = require("node:path");
const { test, expect } = require("@playwright/test");

const RAIZ = path.resolve(__dirname, "../..");
const PAINEL = "file://" + path.join(RAIZ, "painel_precos.html");
const DADOS = fs.readFileSync(path.join(__dirname, "fixtures/painel.json"), "utf8");
const SUPABASE_JS = fs.readFileSync(require.resolve("@supabase/supabase-js/dist/umd/supabase.js"), "utf8");
const AGORA = new Date("2026-09-25T10:40:00Z");
const MARCADAS = ["30C/001", "30C/150", "30C-C/149"];
const ENCOMENDADAS = ["30C/002", "30C/004"];
const IMAGEM = `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="558"><rect width="400" height="558" fill="#9a9890"/></svg>`;

async function abrir(page, { painel = "ok" } = {}) {
  await page.clock.setFixedTime(AGORA);
  await page.route("**/*", rota => (rota.request().url().startsWith("file:") ? rota.continue() : rota.abort()));
  await page.route("https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2", rota =>
    rota.fulfill({ contentType: "application/javascript", body: SUPABASE_JS }));
  await page.route("https://tcgplayer-cdn.tcgplayer.com/**", rota =>
    rota.fulfill({ contentType: "image/svg+xml", body: IMAGEM }));
  await page.route("https://*.supabase.co/rest/v1/**", rota => {
    const url = rota.request().url();
    const json = (corpo, status = 200) => rota.fulfill({ status, contentType: "application/json", body: corpo });
    if (url.includes("/rpc/painel_precos")) {
      return painel === "ok" ? json(DADOS) : json(JSON.stringify({ message: "fora do ar" }), 503);
    }
    if (url.includes("/rpc/atualizar_precos")) {
      return json(JSON.stringify({ ok: false, liberado_em: "2026-09-25T10:45:00+00:00" }));
    }
    for (const [tabela, lista] of [["/cartas_marcadas", MARCADAS], ["/cartas_encomendadas", ENCOMENDADAS]]) {
      if (!url.includes(tabela)) continue;
      if (rota.request().method() === "GET") return json(JSON.stringify(lista.map(carta => ({ carta }))));
      return rota.fulfill({ status: 201, body: "" });
    }
    return json(JSON.stringify({ message: "rota nao simulada" }), 404);
  });
  await page.goto(PAINEL);
  if (painel === "ok") {
    await expect(page.locator("#sub")).toContainText("atualizado em");
    await expect(page.locator("tr.carta").first()).toBeVisible();
  }
  await page.waitForLoadState("networkidle");
  // As imagens visiveis precisam ter terminado de desenhar.
  await page.waitForFunction(() => [...document.images]
    .filter(img => { const r = img.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; })
    .every(img => img.complete));
}

test("lista carregada", async ({ page }) => {
  await abrir(page);
  // Nada pode vazar para o lado: sem rolagem horizontal em nenhuma largura.
  const vazamento = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  expect(vazamento, "largura que sobra alem da tela").toBeLessThanOrEqual(0);
  expect(await cortePelaTabela(page), "largura escondida pelo overflow da tabela").toBeLessThanOrEqual(0);
  await expect(page).toHaveScreenshot("lista.png");
});

// O .tabela tem overflow hidden: o que passar da largura some sem gerar
// rolagem, entao a checagem de vazamento da pagina nao ve.
function cortePelaTabela(page) {
  return page.evaluate(() => {
    const caixa = document.querySelector(".tabela");
    return caixa.querySelector("table").scrollWidth - caixa.clientWidth;
  });
}

test("tela de 320 px", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  await abrir(page);
  expect(await cortePelaTabela(page), "largura escondida pelo overflow da tabela").toBeLessThanOrEqual(0);
  for (const k of ["30C-C/149", "30C-C/106c"]) {
    await page.locator("#f-edicao").selectOption("");
    await page.locator('tr.carta[data-k="' + k + '"]').click();
    await page.mouse.move(0, 0);
    expect(await cortePelaTabela(page), `detalhe de ${k} cortado`).toBeLessThanOrEqual(0);
    await page.locator('tr.carta[data-k="' + k + '"]').click();
  }
  await page.locator("#f-posse").selectOption("a-chegar");
  await expect(page.locator(".tabela")).toHaveScreenshot("estreita-a-chegar.png");
});

test("detalhe da carta", async ({ page }) => {
  await abrir(page);
  await page.locator('tr.carta[data-k="30C-C/149"]').click();
  await page.mouse.move(0, 0); // sem :hover na captura
  const detalhe = page.locator("tr.detalhe");
  await expect(detalhe).toBeVisible();
  await expect(detalhe).toHaveScreenshot("detalhe.png");
});

test("filtro so as que tenho", async ({ page }) => {
  await abrir(page);
  await page.locator("#f-posse").selectOption("tenho");
  await expect(page.locator("tr.carta")).toHaveCount(MARCADAS.length);
  await expect(page.locator(".tabela")).toHaveScreenshot("so-tenho.png");
});

test("filtro so as a chegar", async ({ page }) => {
  await abrir(page);
  await page.locator("#f-posse").selectOption("a-chegar");
  await expect(page.locator("tr.carta")).toHaveCount(ENCOMENDADAS.length);
  await expect(page.locator(".tabela")).toHaveScreenshot("so-a-chegar.png");
});

test("tenho apaga a encomenda", async ({ page }) => {
  await abrir(page);
  const linha = page.locator('tr.carta[data-k="30C/002"]');
  const pedido = linha.getByRole("checkbox", { name: /^Encomendei/ });
  await expect(pedido).toBeChecked();
  await linha.getByRole("checkbox", { name: /^Tenho/ }).check();
  await expect(pedido).not.toBeChecked();
  await expect(pedido).toBeDisabled();
  await expect(linha).toHaveClass(/tenho/);
  await expect(page.locator("tr.detalhe")).toHaveCount(0);
});

test("filtro so as que faltam", async ({ page }) => {
  await abrir(page);
  await page.locator("#f-posse").selectOption("faltam");
  for (const k of [...MARCADAS, ...ENCOMENDADAS]) await expect(page.locator(`tr.carta[data-k="${k}"]`)).toHaveCount(0);
  await expect(page.locator('tr.carta[data-k="30C/003"]')).toHaveCount(1);
});

test("botao com coleta recente", async ({ page }) => {
  await abrir(page);
  await page.locator("#atualizar").click();
  await page.mouse.move(0, 0); // sem :hover na captura
  await expect(page.locator("#estado")).toContainText("o botão libera às 07:45");
  await expect(page.locator(".topo")).toHaveScreenshot("botao-espera.png");
});

test("supabase fora do ar", async ({ page }) => {
  await abrir(page, { painel: "erro" });
  await expect(page.locator("#sub")).toContainText("Não consegui ler os preços");
  await expect(page).toHaveScreenshot("erro.png");
});
