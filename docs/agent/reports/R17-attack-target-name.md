# R17 Attack Target Name Recovery

## Diagnosis

The failure bundle recorded `RecommendationStateError:attack_target_required`
for `操作5号位随从攻击`. The recommendation panel still contained the source
card name (`团队领袖`) and target name (`萨格拉斯`), while the explicit
`目标是对方英雄` target line was missing from the normalized OCR output.

## Change

- Preserve the final trailing name for attack text when at least two name lines
  follow the action.
- Resolve an unnamed attack target by exact normalized name against enemy hero
  and enemy board entities.
- Require exactly one match; zero or duplicate matches return stable errors and
  do not click.
- Leave ordinary attack parsing and starship no-target detection unchanged.

## Verification

- New attack-name fallback tests: 5/5.
- Recommendation-focused regression: 183/183.
- Full headless suite: 1561/1561.
- Architecture, card-rule, and compile gates pass.
