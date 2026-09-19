class Atman < Formula
  desc "Ticket board CLI (atm) for coordinating AI coding agents on one board"
  homepage "https://github.com/advitiyavashist/atman"
  url "https://github.com/advitiyavashist/atman/releases/download/v0.3.0/atman-0.3.0.tar.gz"
  sha256 "c125cb6f91f32e6168cd242c04efd1d09586f2b091451ff5821b747b6943cf33"
  license "MIT"

  depends_on "python@3.13"

  def install
    libexec.install Dir["*"]
    python3 = Formula["python@3.13"].opt_bin/"python3"

    # atm is the primary command; tickets is the same launcher under the
    # compatibility name (one implementation, two PATH entries -- T-809).
    %w[atm tickets].each do |name|
      (bin/name).write <<~SH
        #!/bin/sh
        exec "#{python3}" "#{libexec}/tickets.py" "$@"
      SH
      (bin/name).chmod 0755
    end
  end

  def caveats
    <<~EOS
      `atm` is the primary command; `tickets` is the same launcher under a
      compatibility name. Neither installs a Claude/Codex/Cursor hook by
      itself -- run `atm hooks claude --agent <name>` (or cursor/codex) once
      per project you want the board wired into.

      `brew uninstall atman` removes the binaries and #{opt_libexec} only; it
      never touches ~/.claude or ~/.codex hook configuration you installed
      separately.
    EOS
  end

  test do
    # --version reads the shipped release.json and hashes every file it
    # names, so a real "commit ... (verified release)" answer proves the
    # tarball is not just present but byte-for-byte what was pinned.
    version_output = shell_output("#{bin}/atm --version")
    assert_match(/^tickets commit [0-9a-f]{40} \(verified release\)$/, version_output.strip)
    assert_equal version_output, shell_output("#{bin}/tickets --version")

    board = testpath/".tickets"
    ENV["TICKETS_DIR"] = board.to_s
    ENV["TICKET_AGENT"] = "brew-test"
    ENV["HOME"] = testpath.to_s
    join_output = shell_output("#{bin}/atm join brew-test-seat --roles backend")
    assert_match "joined as brew-test-seat", join_output
    assert_predicate board, :directory?

    port = free_port
    pid = fork do
      exec bin/"atm", "ui", "--port", port.to_s
    end
    begin
      response = nil
      20.times do
        sleep 0.25
        response = begin
          require "net/http"
          Net::HTTP.get_response(URI("http://127.0.0.1:#{port}/board.json"))
        rescue StandardError
          nil
        end
        break if response
      end
      refute_nil response, "atm ui never answered on 127.0.0.1:#{port}"
      assert_equal "200", response.code
      assert_match '"counts"', response.body
    ensure
      Process.kill("TERM", pid)
      Process.wait(pid)
    end
  end
end
