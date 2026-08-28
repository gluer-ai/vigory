/** Example scenario texts for the ingest dialog — quick-fill for trying the
 * extraction agent without writing a prompt from scratch. Fictional.
 *
 * The "Follow-up" samples deliberately re-mention a person/org from an
 * earlier sample (e.g. "Major Ivan Petrov", "3rd Motor Rifle Battalion") to
 * demonstrate multi-scenario ingest: run a base sample, commit it, then run
 * a follow-up — the agent reuses the existing entity instead of duplicating
 * it, so the graph grows instead of resetting per scenario.
 */
export const SAMPLE_SCENARIOS: { title: string; text: string }[] = [
  {
    title: '1. Unit command structure',
    text: 'Major Ivan Petrov commands the 3rd Motor Rifle Battalion, which is based at Kamenka garrison. The battalion is subordinate to the 20th Motor Rifle Division. Petrov reports directly to Colonel Sergei Volkov, the division commander.',
  },
  {
    title: '2. Follow-up: convoy movement',
    text: 'On 14 March, a logistics convoy of 12 trucks departed Belgorod supply depot en route to a forward staging area near Kupyansk. The convoy was escorted by a platoon from the 3rd Motor Rifle Battalion and reportedly carried fuel and ammunition.',
  },
  {
    title: '3. Follow-up: promotion',
    text: 'Ivan Petrov was awarded a commendation and transferred to lead a newly formed reconnaissance company within the 20th Motor Rifle Division.',
  },
  {
    title: 'Facility and personnel link',
    text: 'Signals intelligence indicates that Captain Elena Sokolova is stationed at the Luhansk communications facility, which is operated by the 6th Signal Regiment. Sokolova was previously assigned to a unit based in Rostov-on-Don.',
  },
  {
    title: 'Follow-up: Sokolova reassigned',
    text: 'Captain Elena Sokolova has left the Luhansk communications facility and is now attached to the 3rd Motor Rifle Battalion under Major Ivan Petrov.',
  },
]
