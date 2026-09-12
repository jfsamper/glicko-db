besogo.makeFilePanel = function(container, editor) {
    'use strict';
    var fileChooser, // Reference to the file chooser element
        element, // Scratch variable for creating elements
        WARNING = "Everything not saved will be lost";

    makeNewBoardButton(9); // New 9x9 board button
    makeNewBoardButton(13); // New 13x13 board button
    makeNewBoardButton(19); // New 19x19 board button
    makeNewBoardButton('?'); // New custom board button

    // Hidden file chooser element
    fileChooser = makeFileChooser();
    container.appendChild(fileChooser);

    // Load file button
    element = document.createElement('input');
    element.type = 'button';
    element.value = 'Open';
    element.title = 'Import SGF';
    element.onclick = function() { // Bind click to the hidden file chooser
        fileChooser.click();
    };
    container.appendChild(element);

    // Save file button
    let currentName = 'export.sgf'; // Reference to the current file name.
    element = document.createElement('input');
    element.type = 'button';
    element.value = 'Save';
    element.title = 'Export SGF';
    element.onclick = function() {
        var fileName = prompt('Save file as', currentName);
        if (fileName) { // Canceled or empty string does nothing
            saveFile(fileName, besogo.composeSgf(editor));
            currentName = fileName;
        }
    };
    container.appendChild(element);

    // Copy text button
    element = document.createElement('input');
    element.type = 'button';
    element.value = 'Copy';
    element.title = 'Copy SGF to clipboard';
    element.onclick = function() {
        navigator.clipboard.writeText(besogo.composeSgf(editor))
            .then(function() {
                alert("Copied SGF to clipboard!");
            })
            .catch(function() {
                alert("Failed to write to clipboard");
            });
    }
    container.appendChild(element);

    // Paste text button
    element = document.createElement('input');
    element.type = 'button';
    element.value = 'Paste';
    element.title = 'Paste SGF from clipboard';
    element.onclick = getFromClipboard;
    container.appendChild(element);

    // Makes a new board button
    function makeNewBoardButton(size) {
        var button = document.createElement('input');
        button.type = 'button';
        button.value = size + "x" + size;
        if (size === '?') { // Make button for custom sized board
            button.title = "New custom size board";
            button.onclick = function() {
                var input = prompt("Enter custom size for new board" + "\n" + WARNING, "19:19"),
                    size;
                if (input) { // Canceled or empty string does nothing
                    size = besogo.parseSize(input);
                    editor.loadRoot(besogo.makeGameRoot(size.x, size.y));
                    editor.setGameInfo({});
                }
            };
        } else { // Make button for fixed size board
            button.title = "New " + size + "x" + size + " board";
            button.onclick = function() {
                if (confirm(button.title + "?\n" + WARNING)) {
                    editor.loadRoot(besogo.makeGameRoot(size, size));
                    editor.setGameInfo({});
                }
            };
        }
        container.appendChild(button);
    }

    // Creates the file selector
    function makeFileChooser() {
        var chooser = document.createElement('input');
        chooser.type = 'file';
        chooser.style.display = 'none'; // Keep hidden
        chooser.onchange = readFile; // Read, parse and load on file select
        return chooser;
    }

    function loadGame(game) {
        var sgf;
        try {
            sgf = besogo.parseSgf(game);
        } catch (error) {
            alert('SGF parse error at ' + error.at + ':\n' + error.message);
            return;
        }
        besogo.loadSgf(sgf, editor);
    }

    // Reads, parses and loads an SGF file
    function readFile(evt) {
        var file = evt.target.files[0], // Selected file
            reader = new FileReader(),
            newChooser = makeFileChooser(); // Create new file input to reset selection

        container.replaceChild(newChooser, fileChooser); // Replace with the reset selector
        fileChooser = newChooser;

        reader.onload = function(e){ // Parse and load game tree
            loadGame(e.target.result);
        };
        if (confirm("Load '" + file.name + "'?\n" + WARNING)) {
            currentName = file.name; // Use the file name for the next save
            reader.readAsText(file); // Initiate file read
        }
    }

    // Composes SGF file and initializes download
    function saveFile(fileName, text) {
        var link = document.createElement('a'),
            blob = new Blob([text], { encoding:"UTF-8", type:"text/plain;charset=UTF-8" });

        link.download = fileName; // Set download file name
        link.href = URL.createObjectURL(blob);
        link.style.display = 'none'; // Make link hidden
        container.appendChild(link); // Add link to ensure that clicking works
        link.click(); // Click on link to initiate download
        container.removeChild(link); // Immediately remove the link
    }

    // Asks the user for an SGF file in a text box
    function getFromClipboard() {
        var cont, win, input, cancel, submit;

        cont = document.createElement("div");
        cont.style.position = "fixed";
        cont.style.left = 0;
        cont.style.right = 0;
        cont.style.top = 0;
        cont.style.bottom = 0;
        cont.style.background = "rgba(0, 0, 0, 0.8)";
        cont.onclick = cleanup;

        win = document.createElement("div");
        win.style.position = "absolute";
        win.style.left = "10%";
        win.style.right = "10%";
        win.style.top = "10%";
        win.style.bottom = "10%";
        win.style.background = "#d0fefe";
        win.style.padding = "10px";
        win.onclick = stopBubbling;
        cont.appendChild(win);

        win.appendChild(document.createTextNode("Paste an SGF file here:"));
        win.appendChild(document.createElement("br"));
        input = document.createElement("textarea");
        input.rows = 24;
        input.cols = 80;
        win.appendChild(input);

        win.appendChild(document.createElement("br"));

        cancel = document.createElement("input");
        cancel.type = "button";
        cancel.value = "Cancel";
        cancel.onclick = cleanup;
        win.appendChild(cancel);

        submit = document.createElement("input");
        submit.type = "button";
        submit.value = "Submit";
        submit.onclick = function() {
            loadGame(input.value);
            cleanup();
        };
        win.appendChild(submit);

        function cleanup() {
            cont.remove();
        }

        function stopBubbling(e) {
            // https://stackoverflow.com/questions/1369035/
            e.stopPropagation();
        }

        container.appendChild(cont);
    }
};
