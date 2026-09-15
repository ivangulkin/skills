# Third-party notices

The `ru` skill adapts selected rule definitions, false-positive cases,
tool-artifact regular expressions, and testing ideas from the three
implementations whose MIT notices are reproduced below. This material arrived
here with the `no-ai-slop` skill, which `ru` absorbed; the notices travel with
it. The resulting
workflow is materially different: it does not infer authorship, optimize for
detectors, remove watermarks, or inject deliberate errors.

## Vladimir-Human/humanizer-ru

Source reviewed at commit
`3106135980c9de04889047f52a13f31e6fa06726`:
https://github.com/Vladimir-Human/humanizer-ru

MIT License

Copyright (c) 2026 Vladimir

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## smixs/humanizer-ru

Source reviewed at commit
`91f70df11f7fb30722e6fcf18803d402e2d86a53`:
https://github.com/smixs/humanizer-ru

MIT License

Copyright (c) 2026 Serge Shima

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## ilyautov/humanizer-ru

Source reviewed at commit
`f2d43d6e8d61903660cc3091160d8b987c2ad613`:
https://github.com/ilyautov/humanizer-ru

MIT License

Copyright (c) 2026 Ilya Utov

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Review-only prior art

The repositories below were reviewed for architecture, testing seams, and
false-positive cases. No source code or text from them is vendored in this
repository; `references/prior-art.md` records the conceptual influence:

- https://github.com/petergyang/no-ai-slop (MIT)
- https://github.com/blader/humanizer (MIT)
- https://github.com/SalZaki/antislop (MIT)
- https://github.com/isatimur/de-slop (MIT)
- https://github.com/hwajongpark/slop-gate (MIT)
- https://github.com/tbhb/vale-ai-tells (MIT)
- https://github.com/t0ddharris/slopster (MIT)

## Wikipedia:Signs of AI writing

Selected descriptions of observable tool artifacts and false-positive caveats
are adapted from:
https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing

Wikipedia text is available under the Creative Commons Attribution-ShareAlike
4.0 International License:
https://creativecommons.org/licenses/by-sa/4.0/

The repository links to the living page instead of vendoring its examples.
CC BY-SA attribution applies to descriptions adapted from that page; original
Python implementation and independently written project material remain under
the root MIT license.
