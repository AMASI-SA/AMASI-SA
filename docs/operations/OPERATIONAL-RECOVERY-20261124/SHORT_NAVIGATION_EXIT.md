# Short accounting navigation and read cancellation

Live browser acceptance found a separate navigation defect: on a short screen,
the fixed accounting drawer was shorter than its contents. Its return link was
below the viewport and the drawer did not scroll. Activating the same link with
the keyboard left accounting promptly, distinguishing this layout failure from
report build latency.

The shell now uses the dynamic viewport height with the original `100vh`
fallback and permits vertical scrolling inside the sidebar. Scroll chaining
is contained within it. Destinations, grants, routing and financial reads are
unchanged.

The existing actual-component browser harness adds one required scenario at
three short mobile/desktop sizes. It checks that an ordinary click can reach
the return link while a report is preparing, that the matching read consumer
is released exactly once, that polling stops beyond its steady cadence, and
that no financial POST/PUT/PATCH occurs. The existing thirteen scenarios remain
required; the CI gate now requires fourteen and the new screenshot. The source
manifest additionally binds the tested shell stylesheet and report component.

Local fixture compilation passed. This container denied the sandboxed Chromium
process its socket at launch, before any case executed. That launch failure is
not a browser acceptance pass. The unchanged sandboxed GitHub browser gate
must execute on the final immutable candidate. Production verification of
ordinary click exit remains required after publication.
