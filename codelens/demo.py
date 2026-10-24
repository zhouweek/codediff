"""Honest, deterministic fixtures passed through the production detector."""
import re

SAMPLES = {
    "src/Cart.java": """package demo;
public class Cart {
    public int total(int[] prices) {
        int result = 0;
        for (int price : prices) {
            if (price > 0) { result += price; }
        }
        return result;
    }
    public int discount(int price, int rate) {
        int amount = price * rate / 100;
        if (amount > price) { return 0; }
        return price - amount;
    }
}
""",
    "src/cart.js": """export function total(prices) {
  let result = 0;
  for (const price of prices) {
    if (price > 0) result += price;
  }
  return result;
}

export function discount(price, rate) {
  const amount = price * rate / 100;
  if (amount > price) return 0;
  return price - amount;
}
""",
    "native/cart.c": """int total(int *prices, int count) {
    int result = 0;
    for (int index = 0; index < count; ++index) {
        if (prices[index] > 0) result += prices[index];
    }
    return result;
}
int discount(int price, int rate) {
    int amount = price * rate / 100;
    if (amount > price) return 0;
    return price - amount;
}
""",
    "native/cart.cpp": """#include <vector>
int total(const std::vector<int>& prices) {
    int result = 0;
    for (int price : prices) {
        if (price > 0) { result += price; }
    }
    return result;
}
int discount(int price, int rate) {
    int amount = price * rate / 100;
    if (amount > price) return 0;
    return price - amount;
}
""",
    "src/Cart.cs": """namespace Demo {
public class Cart {
    public int Total(int[] prices) {
        int result = 0;
        foreach (int price in prices) {
            if (price > 0) result += price;
        }
        return result;
    }
    public int Discount(int price, int rate) {
        int amount = price * rate / 100;
        if (amount > price) return 0;
        return price - amount;
    }
}}
""",
    "src/cart.php": """<?php
function total($prices) {
    $result = 0;
    foreach ($prices as $price) {
        if ($price > 0) { $result += $price; }
    }
    return $result;
}
function discount($price, $rate) {
    $amount = $price * $rate / 100;
    if ($amount > $price) { return 0; }
    return $price - $amount;
}
""",
    "pages/cart.html": """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>购物车</title></head>
<body>
  <main class="cart"><h1>购物车</h1>
    <section class="products"><h2>商品列表</h2>
      <ul><li><span>商品一</span><button type="button">移除</button></li></ul>
    </section>
    <footer><p>合计：<strong>100</strong></p><button type="button">结算</button></footer>
  </main>
</body></html>
""",
}


def sources():
    a = [(path, text.encode(), "") for path, text in SAMPLES.items()]
    b = []
    renames = {"prices": "items", "price": "cost", "result": "sum",
               "count": "size", "index": "cursor", "rate": "percent", "amount": "saving"}
    for path, text in SAMPLES.items():
        if path.endswith(".html"):
            updated = text.replace("  <main", "  <!-- rebuilt layout -->\n  <main")
        else:
            updated = re.sub(r"\b(" + "|".join(renames) + r")\b", lambda m: renames[m.group()], text)
            if path.endswith(".js"):
                first, second = updated.split("\n\n")
                updated = "// Helpers have been moved and renamed.\n" + second + "\n\n" + first
            else:
                updated = "// renamed implementation\n" + updated if not path.endswith(".php") else updated.replace("<?php", "<?php\n// renamed implementation")
        b.append((path.replace("src/", "lib/").replace("native/", "core/"), updated.encode(), ""))
    b.append(("lib/unique.py", b"""from datetime import datetime
def describe_clock(zone, format_string):
    current = datetime.now(zone)
    fields = {"year": current.year, "month": current.month, "day": current.day}
    for field, value in fields.items():
        print(field, value)
    return current.strftime(format_string)
""", ""))
    a.append(("node_modules/library.js", b"external dependency", ""))
    return a, b
