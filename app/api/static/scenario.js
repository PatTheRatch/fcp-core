/* The scenario: the roster as the manager is thinking of changing it, held
   beside the roster as it is (docs/design_system.md, "The scenario").

   This file is the SEAM, not the feature. Today a scenario lives exactly as
   long as the page it was made on, and only the week page makes one, from
   the What if answer it has just been given (/design sets one too, as a
   specimen of the bar). Nothing here is persisted and
   no other route reads it. When scenarios become global (kept across pages,
   saved, read by the standings and the trade page), it is this module that
   grows a store behind it; the shell and the pages already talk to it
   through `get`, `set`, `view`, `reset` and `subscribe`, and the state has
   the shape they will keep.

   THE STATE

     active      false until a scenario is set; the bar is drawn only when true
     id          null: a scenario is not stored, so it has no key yet
     name        "Current scenario" until saving exists
     scope       {league, season, team, today}: whose roster, judged on which day
     changes     [{kind: "add" | "drop" | "ir" | "trade", espn_player_id, name,
                   pro_team, position}] -- in the order they were named
     effect      {week, season, playoffs}: each {value, unit, note}, the
                 numbers the change moves, as the route that judged it sent
                 them (value null when that lens says nothing)
     view        "scenario" | "baseline": which of the two the page draws
     source      {route, day, date}: where the numbers came from
     provenance  [[label, html]]: the drawer's account of each number
     saved       false: there is nowhere to save one yet

   Nothing here works a number out. `fromWhatIf` copies three figures out of
   an answer the page already has, and says which fields they were. It
   writes them with pages.js's formatters, so this file loads after that one
   and before shell.js, which draws the bar. */

"use strict";

const SCENARIO = (() => {
  const EMPTY = Object.freeze({
    active: false,
    id: null,
    name: "Current scenario",
    scope: null,
    changes: [],
    effect: { week: null, season: null, playoffs: null },
    view: "scenario",
    source: null,
    provenance: [],
    saved: false,
  });
  let state = EMPTY;
  const listeners = new Set();

  function emit() {
    listeners.forEach((listener) => {
      try {
        listener(state);
      } catch (error) {
        // One reader failing must not stop the others hearing the change.
        console.error(error);
      }
    });
  }

  /** A figure, if the answer has one. */
  const figure = (value, unit, note) =>
    typeof value === "number" && Number.isFinite(value)
      ? { value, unit, note: note || "" }
      : { value: null, unit, note: note || "" };

  /** A scenario out of a `/what-if` answer (app/api/what_if.py, WhatIfOut):
   *  the changes it names and its three numbers -- this week's change in
   *  categories (`week.delta`), the change in an ordinary week from here on
   *  (`judgement.delta_season_per_week`) and the change in a playoff week
   *  (`playoffs.delta_per_week`, when that lens is measurable). */
  function fromWhatIf(answer, scope) {
    const man = (kind) => (player) => ({
      kind,
      espn_player_id: player.espn_player_id,
      name: player.name,
      pro_team: player.pro_team || null,
      position: player.position || null,
    });
    const playoffs = answer.playoffs || null;
    const week = answer.week || {};
    const j = answer.judgement || {};
    const against =
      week.opponent_name === null || week.opponent_name === undefined
        ? ", on a bye"
        : ` against ${week.opponent_name}`;
    // The drawer's account of each number, in the page's own words and
    // through pages.js's own formatters (loaded before this file).
    const provenance = [
      [
        "Judged",
        `Day ${answer.today}${answer.today_date ? ` (${answer.today_date})` : ""}, by the what-if ` +
          `route, built live for this question; nothing is stored.`,
      ],
      [
        "Week",
        `${signed(week.delta, 3)} categories in matchup period ${week.matchup_period}${against}: ` +
          `${fixed(week.expected_before, 2)} expected before, ${fixed(week.expected_after, 2)} after, ` +
          `${count(week.days_remaining, "day")} left.`,
      ],
      [
        "Season",
        `${signed(j.delta_season_per_week, 3)} categories in an ordinary week from here on, over ` +
          `${fixed(j.weeks_remaining, 1)} weeks; with this week, ${signed(j.delta_total, 3)} net. ` +
          `Projected record ${record(j.record_without)} without, ${record(j.record_with)} with.`,
      ],
      [
        "Playoffs",
        !playoffs
          ? "No playoff lens came with this answer."
          : playoffs.measurable
            ? playoffs.line
            : `Nothing to count: ${playoffs.note || "no playoff weeks left"}.`,
      ],
      [
        "The bar",
        `${fixed(answer.hurdle, 2)} categories, from ${answer.hurdle_source}: ${answer.hurdle_note}. ` +
          `This move is ${answer.clears_hurdle ? "over it, which makes it worth a look" : "under it"}; ` +
          `the bar labels a move and hides none.`,
      ],
    ];
    if (answer.finish) {
      provenance.push(["The finish", `${answer.finish.language} ${answer.finish.calibration_note}`]);
    }
    return {
      provenance,
      scope: scope || null,
      changes: [
        ...(answer.adds || []).map(man("add")),
        ...(answer.drops || []).map(man("drop")),
        ...(answer.to_ir || []).map(man("ir")),
      ],
      effect: {
        week: figure(answer.week ? answer.week.delta : null, "cat this week"),
        season: figure(
          answer.judgement ? answer.judgement.delta_season_per_week : null,
          "cat a week",
          answer.judgement ? `over ${answer.judgement.weeks_remaining} weeks` : "",
        ),
        playoffs: figure(
          playoffs && playoffs.measurable ? playoffs.delta_per_week : null,
          "cat a playoff week",
          playoffs ? playoffs.note || "" : "",
        ),
      },
      source: { route: "what-if", day: answer.today, date: answer.today_date || null },
    };
  }

  return {
    EMPTY,
    fromWhatIf,
    get: () => state,
    /** Make `next` the scenario in view. */
    set(next) {
      state = Object.freeze({ ...EMPTY, ...next, active: true, view: "scenario" });
      emit();
    },
    /** Draw the scenario, or the baseline it is measured against. */
    view(which) {
      if (!state.active) return;
      const view = which === "baseline" ? "baseline" : "scenario";
      if (view === state.view) return;
      state = Object.freeze({ ...state, view });
      emit();
    },
    /** Back to the roster as it is. */
    reset() {
      if (!state.active) return;
      state = EMPTY;
      emit();
    },
    /** Hear every change, and the state as it is now. Returns the unsubscribe. */
    subscribe(listener) {
      listeners.add(listener);
      listener(state);
      return () => listeners.delete(listener);
    },
  };
})();
